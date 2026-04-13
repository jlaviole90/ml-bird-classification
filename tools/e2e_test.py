#!/usr/bin/env python3
"""End-to-end testing tool for the bird classification pipeline.

Supports two modes:
  1. CLI — feed a single image or video from the command line
  2. Web — launch a local web UI at http://localhost:9090

Both modes simulate the full pipeline: frame extraction → TorchServe inference →
eBird validation → audit log generation, then display the complete results.

Configuration is loaded from .env (auto-detected in project root) and can be
overridden with CLI flags or environment variables.

Usage:
  # CLI: single image
  python tools/e2e_test.py image path/to/bird.jpg

  # CLI: image with explicit region
  python tools/e2e_test.py image path/to/bird.jpg --region US-NY-109

  # CLI: video (extracts frames at 1 fps)
  python tools/e2e_test.py video path/to/bird.mp4

  # CLI: image against a running TorchServe instance
  python tools/e2e_test.py image path/to/bird.jpg --torchserve http://localhost:8080

  # Web UI
  python tools/e2e_test.py web
  python tools/e2e_test.py web --port 9090

Environment (set in .env or shell):
  EBIRD_API_KEY             — your eBird API key (never stored in code)
  EBIRD_REGION              — eBird subnational2 region code (e.g. US-NY-109)
  EBIRD_LAT                 — camera latitude
  EBIRD_LNG                 — camera longitude
  TORCHSERVE_INFERENCE_URL  — URL to a running TorchServe (default: mock mode)
  VALIDATOR_URL             — URL to the FastAPI catalog (default: mock validation)
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("e2e-test")


def _load_dotenv() -> None:
    """Load .env from the project root into os.environ (no dependencies needed)."""
    for candidate in [
        Path(__file__).resolve().parent.parent / ".env",
        Path.cwd() / ".env",
    ]:
        if candidate.is_file():
            logger.info("Loading config from %s", candidate)
            with open(candidate) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
            return


_load_dotenv()

# ── Data classes for results ────────────────────────────


@dataclass
class CandidateResult:
    rank: int
    species: str
    raw_confidence: float
    on_local_list: bool | None = None
    seasonal_frequency: float | None = None
    adjusted_confidence: float | None = None
    rejection_reason: str | None = None


@dataclass
class FrameResult:
    frame_id: str
    frame_index: int
    timestamp: str
    detection_id: str
    region: str
    top_species: str | None
    top_confidence: float
    ebird_validated: bool
    was_rerouted: bool
    is_notable: bool
    candidates: list[CandidateResult]
    validation_notes: str
    inference_ms: float
    validation_ms: float


# ── Mock inference (when TorchServe isn't running) ──────

MOCK_SPECIES = [
    ("American Robin", "amerob", 0.82),
    ("House Sparrow", "houspa", 0.08),
    ("Northern Cardinal", "norcar", 0.04),
    ("Blue Jay", "blujay", 0.03),
    ("Black-capped Chickadee", "bkcchi", 0.02),
]


def mock_inference(image_bytes: bytes) -> list[dict]:
    """Simulate TorchServe predictions based on image hash for variation."""
    h = hash(image_bytes[:256]) % 100
    species = list(MOCK_SPECIES)
    # Rotate predictions based on hash for variety
    rotation = h % len(species)
    species = species[rotation:] + species[:rotation]
    base_conf = 0.60 + (h % 30) / 100
    predictions = []
    remaining = 1.0
    for i, (name, code, _) in enumerate(species):
        if i == 0:
            conf = base_conf
        else:
            conf = remaining * (0.4 / (i + 1))
        conf = min(conf, remaining)
        remaining -= conf
        predictions.append({
            "species": name,
            "species_code": code,
            "class_id": i,
            "confidence": round(conf, 6),
        })
    return predictions


def real_inference(image_bytes: bytes, torchserve_url: str, model_name: str = "bird_classifier") -> list[dict]:
    """Call a running TorchServe instance."""
    import httpx

    url = f"{torchserve_url}/predictions/{model_name}"
    resp = httpx.post(url, content=image_bytes, headers={"Content-Type": "application/octet-stream"}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("predictions", data if isinstance(data, list) else [data])


# ── Mock validation (when FastAPI isn't running) ────────

MOCK_LOCAL_LIST = {"amerob", "houspa", "norcar", "blujay", "bkcchi", "amegfi", "dowwoo"}
MOCK_FREQUENCIES = {"amerob": 0.85, "houspa": 0.70, "norcar": 0.55, "blujay": 0.40, "bkcchi": 0.60}
MOCK_NOTABLE = {"snobun", "comred"}


def bayesian_adjust(raw: float, freq: float) -> float:
    num = raw * freq
    den = num + (1 - raw) * (1 - freq)
    return num / den if den > 0 else 0.0


def mock_validate(predictions: list[dict], frame_id: str, region: str = "") -> dict:
    """Simulate the eBird validator locally."""
    candidates = []
    for i, p in enumerate(predictions[:5]):
        code = p.get("species_code", p.get("species", ""))
        raw_conf = p.get("confidence", 0.0)
        on_local = code in MOCK_LOCAL_LIST
        freq = MOCK_FREQUENCIES.get(code)

        if on_local and freq and freq > 0:
            adj = bayesian_adjust(raw_conf, freq)
        elif on_local and freq is not None and freq == 0:
            adj = raw_conf * 0.1
        elif not on_local:
            adj = 0.0
        else:
            adj = raw_conf * 0.8

        candidates.append(CandidateResult(
            rank=i + 1,
            species=p.get("species", code),
            raw_confidence=raw_conf,
            on_local_list=on_local,
            seasonal_frequency=freq,
            adjusted_confidence=round(adj, 6),
        ))

    ranked = sorted(candidates, key=lambda c: c.adjusted_confidence or 0, reverse=True)
    accepted = None
    threshold = 0.3

    for c in ranked:
        if (c.adjusted_confidence or 0) < threshold:
            if not c.on_local_list:
                c.rejection_reason = "not_on_local_list"
            elif c.seasonal_frequency == 0:
                c.rejection_reason = "seasonal_frequency_zero"
            else:
                c.rejection_reason = "adjusted_below_threshold"
        elif accepted is not None:
            c.rejection_reason = f"outranked_by_candidate_{accepted.rank}"
        else:
            accepted = c

    for c in ranked:
        if c is not accepted and c.rejection_reason is None:
            c.rejection_reason = f"outranked_by_candidate_{accepted.rank}" if accepted else "adjusted_below_threshold"

    is_notable = accepted is not None and accepted.species.lower().replace(" ", "") in {
        c.replace(" ", "") for c in MOCK_NOTABLE
    }
    was_rerouted = accepted is not None and accepted.rank != 1

    return {
        "species_code": accepted.species if accepted else None,
        "common_name": accepted.species if accepted else None,
        "raw_confidence": predictions[0]["confidence"] if predictions else 0.0,
        "adjusted_confidence": accepted.adjusted_confidence if accepted else 0.0,
        "ebird_validated": accepted is not None,
        "was_rerouted": was_rerouted,
        "is_notable": is_notable,
        "validation_notes": f"[{region or 'mock'}] {'Rerouted' if was_rerouted else 'Accepted'} {accepted.species}" if accepted else f"[{region or 'mock'}] All candidates rejected",
        "candidates": [asdict(c) for c in candidates],
    }


def real_validate(predictions: list[dict], frame_id: str, detection_id: str,
                  validator_url: str, region: str = "") -> dict:
    """Call the running FastAPI /validate endpoint."""
    import httpx

    payload: dict[str, Any] = {
        "frame_id": frame_id,
        "predictions": predictions,
    }
    if region:
        payload["region"] = region

    resp = httpx.post(f"{validator_url}/api/v1/validate", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ── Frame extraction ────────────────────────────────────


def extract_frames_from_image(path: str) -> list[tuple[int, bytes]]:
    """Load a single image file as one frame."""
    with open(path, "rb") as f:
        return [(0, f.read())]


def extract_frames_from_video(path: str, fps: float = 1.0) -> list[tuple[int, bytes]]:
    """Extract frames from a video at the specified FPS."""
    try:
        import cv2
    except ImportError:
        logger.error("opencv-python is required for video mode. Install: pip install opencv-python-headless")
        sys.exit(1)

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        logger.error("Cannot open video: %s", path)
        sys.exit(1)

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(video_fps / fps))
    frames = []
    idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if idx % frame_interval == 0:
            _, buf = cv2.imencode(".jpg", frame)
            frames.append((idx, buf.tobytes()))
        idx += 1

    cap.release()
    logger.info("Extracted %d frames from %s (video FPS=%.1f, sample every %d)",
                len(frames), path, video_fps, frame_interval)
    return frames


# ── Pipeline runner ─────────────────────────────────────


def run_pipeline(
    frames: list[tuple[int, bytes]],
    torchserve_url: str | None = None,
    validator_url: str | None = None,
    model_name: str = "bird_classifier",
    region: str = "",
) -> list[FrameResult]:
    """Run each frame through inference + validation and return results."""
    results = []
    use_real_ts = torchserve_url is not None
    use_real_val = validator_url is not None
    region = region or os.environ.get("EBIRD_REGION", "")

    if region:
        logger.info("eBird region: %s", region)

    for frame_idx, frame_bytes in frames:
        frame_id = str(uuid.uuid4())[:8]
        detection_id = str(uuid.uuid4())

        # Inference
        inference_fallback = False
        t0 = time.perf_counter()
        try:
            if use_real_ts:
                predictions = real_inference(frame_bytes, torchserve_url, model_name)
            else:
                predictions = mock_inference(frame_bytes)
        except Exception as e:
            logger.warning("TorchServe inference failed for frame %d: %s — falling back to mock", frame_idx, e)
            predictions = mock_inference(frame_bytes)
            inference_fallback = True
        inference_ms = (time.perf_counter() - t0) * 1000

        # Validation
        t1 = time.perf_counter()
        try:
            if use_real_val:
                val = real_validate(predictions, frame_id, detection_id, validator_url, region)
            else:
                val = mock_validate(predictions, frame_id, region)
        except Exception as e:
            logger.error("Validation failed for frame %d: %s", frame_idx, e)
            val = {"ebird_validated": False, "candidates": [], "validation_notes": str(e)}
        validation_ms = (time.perf_counter() - t1) * 1000

        candidates = [
            CandidateResult(**c) if isinstance(c, dict) else c
            for c in val.get("candidates", [])
        ]

        result = FrameResult(
            frame_id=frame_id,
            frame_index=frame_idx,
            timestamp=datetime.now(timezone.utc).isoformat(),
            detection_id=detection_id,
            region=region,
            top_species=val.get("common_name") or val.get("species_code"),
            top_confidence=val.get("adjusted_confidence", 0.0),
            ebird_validated=val.get("ebird_validated", False),
            was_rerouted=val.get("was_rerouted", False),
            is_notable=val.get("is_notable", False),
            candidates=candidates,
            validation_notes=("⚠ TorchServe unavailable — used mock inference. " if inference_fallback else "") + val.get("validation_notes", ""),
            inference_ms=round(inference_ms, 2),
            validation_ms=round(validation_ms, 2),
        )
        results.append(result)

    return results


# ── CLI display ─────────────────────────────────────────


def print_results(results: list[FrameResult], mode: str) -> None:
    """Pretty-print results to the terminal."""
    region = results[0].region if results else os.environ.get("EBIRD_REGION", "")

    print(f"\n{'=' * 70}")
    print(f"  Bird Classification E2E Results — {len(results)} frame(s) processed")
    if region:
        print(f"  eBird Region: {region}")
    print(f"{'=' * 70}\n")

    for r in results:
        status = "VALIDATED" if r.ebird_validated else "REJECTED"
        reroute = " [REROUTED]" if r.was_rerouted else ""
        notable = " [NOTABLE]" if r.is_notable else ""

        print(f"Frame #{r.frame_index}  ({r.frame_id})")
        print(f"  Status:     {status}{reroute}{notable}")
        print(f"  Species:    {r.top_species or 'None'}")
        print(f"  Confidence: {r.top_confidence:.4f}")
        print(f"  Inference:  {r.inference_ms:.1f}ms")
        print(f"  Validation: {r.validation_ms:.1f}ms")
        print(f"  Notes:      {r.validation_notes}")
        print()

        if r.candidates:
            print("  Candidates:")
            print(f"  {'Rank':<6}{'Species':<25}{'Raw':>8}{'Adj':>8}{'Local':>7}{'Freq':>7}  Rejection")
            print(f"  {'─' * 80}")
            for c in sorted(r.candidates, key=lambda x: x.rank):
                local = "✓" if c.on_local_list else "✗" if c.on_local_list is not None else "?"
                freq = f"{c.seasonal_frequency:.2f}" if c.seasonal_frequency is not None else "—"
                adj = f"{c.adjusted_confidence:.4f}" if c.adjusted_confidence is not None else "—"
                rej = c.rejection_reason or "ACCEPTED"
                marker = "→ " if c.rejection_reason is None else "  "
                print(f"  {marker}{c.rank:<4}{c.species:<25}{c.raw_confidence:>8.4f}{adj:>8}{local:>7}{freq:>7}  {rej}")
            print()

        print(f"  {'─' * 60}\n")

    # Summary
    validated = sum(1 for r in results if r.ebird_validated)
    rerouted = sum(1 for r in results if r.was_rerouted)
    notable = sum(1 for r in results if r.is_notable)
    avg_inf = sum(r.inference_ms for r in results) / len(results) if results else 0
    avg_val = sum(r.validation_ms for r in results) / len(results) if results else 0

    print(f"Summary: {validated}/{len(results)} validated, {rerouted} rerouted, {notable} notable")
    print(f"Avg inference: {avg_inf:.1f}ms, Avg validation: {avg_val:.1f}ms")

    if mode in ("image", "video"):
        print(f"\nJSON output: pipe with --json flag for machine-readable output")


# ── Web UI ──────────────────────────────────────────────


WEB_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bird Pipeline E2E Tester</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, -apple-system, sans-serif; background: #f3f4f6; color: #1a1a2e; }
  .container { max-width: 900px; margin: 2rem auto; padding: 0 1.5rem; }
  h1 { text-align: center; font-size: 1.8rem; margin-bottom: 0.5rem; }
  .subtitle { text-align: center; color: #6b7280; margin-bottom: 2rem; }
  .upload-area {
    border: 2px dashed #d1d5db; border-radius: 12px; padding: 3rem 2rem;
    text-align: center; background: #fff; cursor: pointer; transition: border-color 0.2s;
  }
  .upload-area:hover, .upload-area.dragover { border-color: #059669; background: #f0fdf4; }
  .upload-area input { display: none; }
  .upload-area p { color: #6b7280; margin-top: 0.5rem; font-size: 0.9rem; }
  .btn { display: inline-block; padding: 0.6rem 1.5rem; border-radius: 8px; border: none;
    font-size: 0.95rem; cursor: pointer; font-weight: 600; transition: all 0.2s; }
  .btn-primary { background: #059669; color: #fff; }
  .btn-primary:hover { background: #047857; }
  .btn-primary:disabled { background: #9ca3af; cursor: not-allowed; }
  .config { display: flex; gap: 1rem; margin: 1.5rem 0; flex-wrap: wrap; }
  .config label { font-size: 0.85rem; color: #6b7280; }
  .config input { padding: 0.4rem 0.6rem; border: 1px solid #d1d5db; border-radius: 6px;
    font-size: 0.9rem; width: 100%; margin-top: 0.25rem; }
  .config .field { flex: 1; min-width: 200px; }
  #status { margin: 1rem 0; padding: 0.75rem; border-radius: 8px; display: none; }
  #status.info { display: block; background: #dbeafe; color: #1e40af; }
  #status.success { display: block; background: #d1fae5; color: #065f46; }
  #status.error { display: block; background: #fee2e2; color: #991b1b; }
  .results { margin-top: 2rem; }
  .frame-card { background: #fff; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .frame-header { display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 1rem; flex-wrap: wrap; gap: 0.5rem; }
  .frame-title { font-size: 1.1rem; font-weight: 700; }
  .badge { padding: 0.2rem 0.6rem; border-radius: 20px; font-size: 0.75rem; font-weight: 700; }
  .badge-validated { background: #d1fae5; color: #065f46; }
  .badge-rejected { background: #fee2e2; color: #991b1b; }
  .badge-rerouted { background: #fef3c7; color: #92400e; }
  .badge-notable { background: #dbeafe; color: #1e40af; }
  .species-name { font-size: 1.3rem; font-weight: 700; color: #059669; }
  .meta { font-size: 0.85rem; color: #6b7280; margin: 0.5rem 0; }
  table { width: 100%; border-collapse: collapse; margin-top: 1rem; font-size: 0.85rem; }
  th { text-align: left; padding: 0.5rem; border-bottom: 2px solid #e5e7eb; color: #6b7280; font-weight: 600; }
  td { padding: 0.5rem; border-bottom: 1px solid #f3f4f6; }
  tr.accepted td { background: #f0fdf4; font-weight: 600; }
  .notes { margin-top: 1rem; padding: 0.75rem; background: #f9fafb; border-radius: 8px;
    font-size: 0.85rem; color: #4b5563; }
  .summary-bar { background: #fff; border-radius: 12px; padding: 1.25rem; margin-top: 1.5rem;
    display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 1rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08); text-align: center; }
  .summary-val { font-size: 1.5rem; font-weight: 700; color: #059669; }
  .summary-lbl { font-size: 0.8rem; color: #6b7280; }
  .preview-img { max-width: 200px; max-height: 150px; border-radius: 8px; margin-top: 0.5rem; }
</style>
</head>
<body>
<div class="container">
  <h1>Bird Pipeline E2E Tester</h1>
  <p class="subtitle">Feed an image or video to test the full classification + eBird validation pipeline</p>

  <div class="upload-area" id="dropZone" onclick="document.getElementById('fileInput').click()">
    <button class="btn btn-primary">Choose File</button>
    <p>or drag & drop an image (.jpg, .png) or video (.mp4, .avi, .webm)</p>
    <input type="file" id="fileInput" accept="image/*,video/*">
    <div id="preview"></div>
  </div>

  <div class="config" id="configBar">
    <div class="field">
      <label>eBird Region</label>
      <input type="text" id="ebirdRegion" placeholder="US-NY-109" value="__EBIRD_REGION__">
    </div>
    <div class="field">
      <label>Video FPS</label>
      <input type="number" id="videoFps" value="1" min="0.1" max="30" step="0.5">
    </div>
  </div>
  <input type="hidden" id="torchserveUrl" value="__TORCHSERVE_URL__">
  <input type="hidden" id="validatorUrl" value="__VALIDATOR_URL__">
  <div id="modeTag" style="text-align:center; margin: 0.5rem 0;">
    <span style="background:__MODE_BG__; color:__MODE_FG__; padding:0.2rem 0.8rem; border-radius:20px; font-size:0.8rem; font-weight:600;">__MODE_LABEL__</span>
  </div>

  <div style="text-align: center; margin: 1rem 0;">
    <button class="btn btn-primary" id="runBtn" disabled onclick="runTest()">Run Pipeline</button>
  </div>

  <div id="status"></div>
  <div class="results" id="results"></div>
</div>

<script>
let selectedFile = null;

const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const runBtn = document.getElementById('runBtn');
const statusEl = document.getElementById('status');
const resultsEl = document.getElementById('results');

['dragenter', 'dragover'].forEach(e => dropZone.addEventListener(e, ev => { ev.preventDefault(); dropZone.classList.add('dragover'); }));
['dragleave', 'drop'].forEach(e => dropZone.addEventListener(e, ev => { ev.preventDefault(); dropZone.classList.remove('dragover'); }));
dropZone.addEventListener('drop', ev => { if (ev.dataTransfer.files.length) handleFile(ev.dataTransfer.files[0]); });
fileInput.addEventListener('change', ev => { if (ev.target.files.length) handleFile(ev.target.files[0]); });

function handleFile(file) {
  selectedFile = file;
  runBtn.disabled = false;
  const preview = document.getElementById('preview');
  if (file.type.startsWith('image/')) {
    const url = URL.createObjectURL(file);
    preview.innerHTML = `<img src="${url}" class="preview-img"><p><strong>${file.name}</strong> (${(file.size/1024).toFixed(1)} KB)</p>`;
  } else {
    preview.innerHTML = `<p><strong>${file.name}</strong> (${(file.size/1024/1024).toFixed(1)} MB)</p>`;
  }
}

function setStatus(msg, type) {
  statusEl.className = type;
  statusEl.textContent = msg;
}

async function runTest() {
  if (!selectedFile) return;
  runBtn.disabled = true;
  setStatus('Processing...', 'info');
  resultsEl.innerHTML = '';

  const formData = new FormData();
  formData.append('file', selectedFile);
  formData.append('region', document.getElementById('ebirdRegion').value);
  formData.append('torchserve_url', document.getElementById('torchserveUrl').value);
  formData.append('validator_url', document.getElementById('validatorUrl').value);
  formData.append('video_fps', document.getElementById('videoFps').value);

  try {
    const resp = await fetch('/api/run', { method: 'POST', body: formData });
    const data = await resp.json();
    if (data.error) { setStatus('Error: ' + data.error, 'error'); runBtn.disabled = false; return; }
    const rgn = data.region || '';
    setStatus(`Processed ${data.results.length} frame(s) successfully` + (rgn ? ` — Region: ${rgn}` : ''), 'success');
    renderResults(data.results);
  } catch (e) {
    setStatus('Request failed: ' + e.message, 'error');
  }
  runBtn.disabled = false;
}

function renderResults(results) {
  let html = '';
  for (const r of results) {
    const badges = [];
    if (r.ebird_validated) badges.push('<span class="badge badge-validated">VALIDATED</span>');
    else badges.push('<span class="badge badge-rejected">REJECTED</span>');
    if (r.was_rerouted) badges.push('<span class="badge badge-rerouted">REROUTED</span>');
    if (r.is_notable) badges.push('<span class="badge badge-notable">NOTABLE</span>');

    let rows = '';
    const cands = (r.candidates || []).sort((a,b) => a.rank - b.rank);
    for (const c of cands) {
      const accepted = !c.rejection_reason;
      const cls = accepted ? 'accepted' : '';
      const local = c.on_local_list === true ? '✓' : c.on_local_list === false ? '✗' : '?';
      const freq = c.seasonal_frequency != null ? c.seasonal_frequency.toFixed(2) : '—';
      const adj = c.adjusted_confidence != null ? c.adjusted_confidence.toFixed(4) : '—';
      const rej = c.rejection_reason || '✓ ACCEPTED';
      rows += `<tr class="${cls}"><td>${c.rank}</td><td>${c.species}</td><td>${c.raw_confidence.toFixed(4)}</td><td>${adj}</td><td>${local}</td><td>${freq}</td><td>${rej}</td></tr>`;
    }

    html += `
      <div class="frame-card">
        <div class="frame-header">
          <span class="frame-title">Frame #${r.frame_index}</span>
          <div>${badges.join(' ')}</div>
        </div>
        <div class="species-name">${r.top_species || 'No species accepted'}</div>
        <div class="meta">
          Confidence: ${r.top_confidence.toFixed(4)} &bull;
          Inference: ${r.inference_ms.toFixed(1)}ms &bull;
          Validation: ${r.validation_ms.toFixed(1)}ms
        </div>
        <table>
          <thead><tr><th>Rank</th><th>Species</th><th>Raw</th><th>Adjusted</th><th>Local</th><th>Freq</th><th>Decision</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div class="notes">${r.validation_notes}</div>
      </div>`;
  }

  const validated = results.filter(r => r.ebird_validated).length;
  const rerouted = results.filter(r => r.was_rerouted).length;
  const notable = results.filter(r => r.is_notable).length;
  const avgInf = results.reduce((s, r) => s + r.inference_ms, 0) / results.length;
  const avgVal = results.reduce((s, r) => s + r.validation_ms, 0) / results.length;

  html += `
    <div class="summary-bar">
      <div><div class="summary-val">${results.length}</div><div class="summary-lbl">Frames</div></div>
      <div><div class="summary-val">${validated}</div><div class="summary-lbl">Validated</div></div>
      <div><div class="summary-val">${rerouted}</div><div class="summary-lbl">Rerouted</div></div>
      <div><div class="summary-val">${notable}</div><div class="summary-lbl">Notable</div></div>
      <div><div class="summary-val">${avgInf.toFixed(0)}ms</div><div class="summary-lbl">Avg Inference</div></div>
      <div><div class="summary-val">${avgVal.toFixed(0)}ms</div><div class="summary-lbl">Avg Validation</div></div>
    </div>`;

  resultsEl.innerHTML = html;
}
</script>
</body>
</html>"""


def start_web_server(host: str, port: int, torchserve_url: str | None,
                     validator_url: str | None, region: str = "") -> None:
    """Start a lightweight web server for the E2E test UI."""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import tempfile

    server_region = region or os.environ.get("EBIRD_REGION", "")
    is_live = torchserve_url is not None or validator_url is not None

    ts_url_val = torchserve_url or ""
    val_url_val = validator_url or ""
    mode_label = "LIVE — full integration test" if is_live else "MOCK MODE"
    mode_bg = "#d1fae5" if is_live else "#fef3c7"
    mode_fg = "#065f46" if is_live else "#92400e"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/" or self.path == "/index.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                html = (WEB_HTML
                    .replace("__EBIRD_REGION__", server_region)
                    .replace("__TORCHSERVE_URL__", ts_url_val)
                    .replace("__VALIDATOR_URL__", val_url_val)
                    .replace("__MODE_LABEL__", mode_label)
                    .replace("__MODE_BG__", mode_bg)
                    .replace("__MODE_FG__", mode_fg))
                self.wfile.write(html.encode())
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path != "/api/run":
                self.send_error(404)
                return

            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._json_response(400, {"error": "Expected multipart/form-data"})
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)

            import email.parser
            import email.policy

            msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
                b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
            )

            file_data = None
            file_name = ""
            ts_url = torchserve_url
            val_url = validator_url
            req_region = server_region
            video_fps = 1.0

            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if name == "file":
                    file_data = part.get_payload(decode=True)
                    file_name = part.get_filename() or "upload"
                elif name == "region":
                    val = part.get_payload(decode=True).decode().strip()
                    if val:
                        req_region = val
                elif name == "torchserve_url":
                    val = part.get_payload(decode=True).decode().strip()
                    if val:
                        ts_url = val
                elif name == "validator_url":
                    val = part.get_payload(decode=True).decode().strip()
                    if val:
                        val_url = val
                elif name == "video_fps":
                    try:
                        video_fps = float(part.get_payload(decode=True).decode().strip())
                    except (ValueError, TypeError):
                        pass

            if not file_data:
                self._json_response(400, {"error": "No file uploaded"})
                return

            ext = Path(file_name).suffix.lower()
            is_video = ext in (".mp4", ".avi", ".mov", ".webm", ".mkv")

            if is_video:
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                    tmp.write(file_data)
                    tmp_path = tmp.name
                try:
                    frames = extract_frames_from_video(tmp_path, fps=video_fps)
                finally:
                    os.unlink(tmp_path)
            else:
                frames = [(0, file_data)]

            results = run_pipeline(frames, ts_url, val_url, region=req_region)
            self._json_response(200, {"region": req_region, "results": [asdict(r) for r in results]})

        def _json_response(self, code, data):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode())

        def log_message(self, format, *args):
            logger.info(format, *args)

    server = HTTPServer((host, port), Handler)
    logger.info("E2E Test Web UI running at http://%s:%d", host, port)
    logger.info("Open in your browser to upload images/videos for testing")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down web server")
        server.shutdown()


# ── CLI entry point ─────────────────────────────────────


LIVE_DEFAULTS = {
    "torchserve": "http://localhost:8080",
    "validator": "http://localhost:8000",
}


def _apply_live(args: argparse.Namespace) -> None:
    """When --live is set, fill in localhost URLs for any that aren't explicitly provided."""
    if not getattr(args, "live", False):
        return
    if not args.torchserve:
        args.torchserve = LIVE_DEFAULTS["torchserve"]
    if not args.validator:
        args.validator = LIVE_DEFAULTS["validator"]
    logger.info("Live mode: TorchServe=%s  Validator=%s", args.torchserve, args.validator)


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end test tool for the bird classification pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    region_help = "eBird region code, e.g. US-NY-109 (default: from .env / EBIRD_REGION)"
    live_help = "Use live services (TorchServe + catalog) on localhost instead of mocks"

    img_parser = sub.add_parser("image", help="Process a single image")
    img_parser.add_argument("path", help="Path to image file (.jpg, .png)")
    img_parser.add_argument("--live", action="store_true", help=live_help)
    img_parser.add_argument("--torchserve", default=os.environ.get("TORCHSERVE_INFERENCE_URL"),
                            help="TorchServe URL (default: mock mode)")
    img_parser.add_argument("--validator", default=os.environ.get("VALIDATOR_URL"),
                            help="FastAPI validator URL (default: mock mode)")
    img_parser.add_argument("--region", default=os.environ.get("EBIRD_REGION", ""), help=region_help)
    img_parser.add_argument("--json", action="store_true", help="Output as JSON")

    vid_parser = sub.add_parser("video", help="Process a video file")
    vid_parser.add_argument("path", help="Path to video file (.mp4, .avi)")
    vid_parser.add_argument("--live", action="store_true", help=live_help)
    vid_parser.add_argument("--fps", type=float, default=1.0, help="Frame extraction rate (default: 1)")
    vid_parser.add_argument("--torchserve", default=os.environ.get("TORCHSERVE_INFERENCE_URL"))
    vid_parser.add_argument("--validator", default=os.environ.get("VALIDATOR_URL"))
    vid_parser.add_argument("--region", default=os.environ.get("EBIRD_REGION", ""), help=region_help)
    vid_parser.add_argument("--json", action="store_true")

    web_parser = sub.add_parser("web", help="Launch web UI for interactive testing")
    web_parser.add_argument("--live", action="store_true", help=live_help)
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=9090)
    web_parser.add_argument("--torchserve", default=os.environ.get("TORCHSERVE_INFERENCE_URL"))
    web_parser.add_argument("--validator", default=os.environ.get("VALIDATOR_URL"))
    web_parser.add_argument("--region", default=os.environ.get("EBIRD_REGION", ""), help=region_help)

    args = parser.parse_args()
    _apply_live(args)

    if args.command == "image":
        if not Path(args.path).exists():
            logger.error("File not found: %s", args.path)
            sys.exit(1)
        frames = extract_frames_from_image(args.path)
        results = run_pipeline(frames, args.torchserve, args.validator, region=args.region)
        if args.json:
            print(json.dumps([asdict(r) for r in results], indent=2, default=str))
        else:
            print_results(results, "image")

    elif args.command == "video":
        if not Path(args.path).exists():
            logger.error("File not found: %s", args.path)
            sys.exit(1)
        frames = extract_frames_from_video(args.path, fps=args.fps)
        results = run_pipeline(frames, args.torchserve, args.validator, region=args.region)
        if args.json:
            print(json.dumps([asdict(r) for r in results], indent=2, default=str))
        else:
            print_results(results, "video")

    elif args.command == "web":
        start_web_server(args.host, args.port, args.torchserve, args.validator, args.region)


if __name__ == "__main__":
    main()
