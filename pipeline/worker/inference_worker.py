"""Inference worker: pulls RTSP frames, detects motion, classifies birds.

Runs as a long-lived process. On motion detection, sends frames to TorchServe
for species classification and forwards predictions to the Catalog API for
eBird validation and storage.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("inference-worker")


def _resolve_env(val: str) -> str:
    if isinstance(val, str) and val.startswith("${"):
        inner = val[2:-1]
        var, _, default = inner.partition(":-")
        return os.environ.get(var, default)
    return val


def load_config(path: str | Path = "pipeline/worker/config.yaml") -> dict[str, Any]:
    with open(path) as f:
        raw = yaml.safe_load(f)

    def walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        if isinstance(obj, str):
            return _resolve_env(obj)
        return obj

    return walk(raw)


class MotionDetector:
    """Detect motion between consecutive frames using absolute pixel difference."""

    def __init__(self, threshold: int = 30, min_area_fraction: float = 0.005):
        self.threshold = threshold
        self.min_area_fraction = min_area_fraction
        self._prev_gray: np.ndarray | None = None

    def detect(self, frame: np.ndarray) -> bool:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self._prev_gray is None:
            self._prev_gray = gray
            return False

        delta = cv2.absdiff(self._prev_gray, gray)
        self._prev_gray = gray

        _, thresh = cv2.threshold(delta, self.threshold, 255, cv2.THRESH_BINARY)
        motion_fraction = np.count_nonzero(thresh) / thresh.size
        return motion_fraction > self.min_area_fraction


class InferenceWorker:
    def __init__(self, cfg: dict[str, Any]):
        self.rtsp_url = cfg["stream"]["url"]
        self.base_fps = float(cfg["stream"]["base_fps"])
        self.motion_fps = float(cfg["stream"]["motion_fps"])
        self.jpeg_quality = int(cfg["stream"]["jpeg_quality"])
        self.max_dim = int(cfg["stream"]["max_dimension"])

        self.torchserve_url = cfg["torchserve"]["url"]
        self.model_name = cfg["torchserve"]["model_name"]
        self.catalog_url = cfg["catalog"]["url"]
        self.ebird_region = cfg["catalog"]["ebird_region"]

        self.motion = MotionDetector(
            threshold=int(cfg["stream"]["motion_threshold"]),
        )
        self.client = httpx.Client(timeout=30.0)

        self._running = True
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

        self._consecutive_errors = 0
        self._max_errors = 20

    def _stop(self, *_: Any) -> None:
        logger.info("Shutdown signal received")
        self._running = False

    def _open_stream(self) -> subprocess.Popen:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-rtsp_transport", "tcp",
            "-i", self.rtsp_url,
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-vf", f"scale='min({self.max_dim},iw)':'min({self.max_dim},ih)':force_original_aspect_ratio=decrease",
            "-an", "-sn", "pipe:1",
        ]
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10**8)

    def _probe_resolution(self) -> tuple[int, int]:
        cmd = [
            "ffprobe", "-v", "error",
            "-rtsp_transport", "tcp",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            self.rtsp_url,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            info = json.loads(result.stdout)
            stream = info["streams"][0]
            w, h = int(stream["width"]), int(stream["height"])
            scale = min(1.0, self.max_dim / max(w, h))
            return int(w * scale), int(h * scale)
        except Exception:
            logger.warning("ffprobe failed — using 1280x720 fallback")
            return 1280, 720

    def _encode_jpeg(self, frame: np.ndarray) -> bytes:
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        return buf.tobytes()

    def _classify(self, jpeg: bytes) -> list[dict]:
        url = f"{self.torchserve_url}/predictions/{self.model_name}"
        resp = self.client.post(url, content=jpeg, headers={"Content-Type": "application/octet-stream"})
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "predictions" in data:
            return data["predictions"]
        if isinstance(data, list) and data and "predictions" in data[0]:
            return data[0]["predictions"]
        return data if isinstance(data, list) else []

    def _validate(self, predictions: list[dict], frame_id: str) -> dict:
        url = f"{self.catalog_url}/api/v1/validate"
        payload = {
            "frame_id": frame_id,
            "predictions": predictions,
            "inference_latency_ms": 0.0,
        }
        resp = self.client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def run(self) -> None:
        logger.info("Starting inference worker")
        logger.info("  RTSP: %s", self.rtsp_url.split("@")[-1])
        logger.info("  TorchServe: %s", self.torchserve_url)
        logger.info("  Catalog: %s", self.catalog_url)
        logger.info("  Region: %s", self.ebird_region)

        width, height = self._probe_resolution()
        frame_size = width * height * 3
        logger.info("  Resolution: %dx%d (%d bytes/frame)", width, height, frame_size)

        proc = self._open_stream()
        interval = 1.0 / self.base_fps
        last_capture = 0.0
        frames_processed = 0
        detections = 0

        while self._running:
            raw = proc.stdout.read(frame_size)
            if len(raw) < frame_size:
                logger.warning("Stream read error — reconnecting in 5s")
                proc.terminate()
                proc.wait()
                time.sleep(5)
                proc = self._open_stream()
                self.motion = MotionDetector(
                    threshold=self.motion.threshold,
                    min_area_fraction=self.motion.min_area_fraction,
                )
                continue

            now = time.time()
            frame = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
            has_motion = self.motion.detect(frame)

            current_interval = (1.0 / self.motion_fps) if has_motion else interval
            if now - last_capture < current_interval:
                continue
            last_capture = now

            if not has_motion:
                continue

            frames_processed += 1
            frame_id = str(uuid.uuid4())
            ts = datetime.now(timezone.utc).isoformat()

            try:
                jpeg = self._encode_jpeg(frame)
                t0 = time.perf_counter()
                predictions = self._classify(jpeg)
                inference_ms = (time.perf_counter() - t0) * 1000

                validated = self._validate(predictions, frame_id)

                species = validated.get("common_name", "unknown")
                confidence = validated.get("adjusted_confidence", 0)
                status = "validated" if validated.get("ebird_validated") else "unvalidated"
                rerouted = validated.get("was_rerouted", False)

                reroute_tag = " [REROUTED]" if rerouted else ""
                logger.info(
                    "Detection #%d | %s (%.1f%%) | %s%s | %.0fms | %s",
                    detections + 1, species, confidence * 100,
                    status, reroute_tag, inference_ms, frame_id[:8],
                )
                detections += 1
                self._consecutive_errors = 0

            except Exception:
                self._consecutive_errors += 1
                logger.exception(
                    "Error processing frame %s (consecutive: %d)",
                    frame_id[:8], self._consecutive_errors,
                )
                if self._consecutive_errors >= self._max_errors:
                    logger.error("Too many consecutive errors — exiting for restart")
                    break

        proc.terminate()
        proc.wait()
        self.client.close()
        logger.info(
            "Worker stopped — %d frames processed, %d detections",
            frames_processed, detections,
        )


def main() -> None:
    cfg = load_config()
    worker = InferenceWorker(cfg)
    worker.run()


if __name__ == "__main__":
    main()
