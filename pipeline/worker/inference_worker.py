"""Inference worker: pulls RTSP frames, detects motion, classifies birds.

Runs as a long-lived process. On motion detection, sends frames to TorchServe
for species classification and forwards predictions to the Catalog API for
eBird validation and storage.

When a bird is detected, enters a recording session that captures every frame
during the cooldown period. Recording continues as long as new detections keep
occurring, and stops once the cooldown expires without a detection.
"""

from __future__ import annotations

import base64
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

from logging.handlers import RotatingFileHandler

LOG_DIR = Path("/app/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            LOG_DIR / "inference.log",
            maxBytes=50 * 1024 * 1024,
            backupCount=10,
        ),
    ],
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
    """Detect localised, bird-sized motion between consecutive frames.

    Rejects:
    - Scattered pixel noise (wind in leaves, lighting shifts) via contour filtering
    - Whole-frame changes (camera adjusting exposure) via max_area_fraction
    """

    def __init__(
        self,
        threshold: int = 30,
        min_area_fraction: float = 0.005,
        max_area_fraction: float = 0.6,
        min_contour_area: int = 500,
    ):
        self.threshold = threshold
        self.min_area_fraction = min_area_fraction
        self.max_area_fraction = max_area_fraction
        self.min_contour_area = min_contour_area
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
        thresh = cv2.dilate(thresh, None, iterations=2)

        motion_fraction = np.count_nonzero(thresh) / thresh.size

        if motion_fraction < self.min_area_fraction:
            return False
        if motion_fraction > self.max_area_fraction:
            return False

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        significant = [c for c in contours if cv2.contourArea(c) >= self.min_contour_area]

        return len(significant) > 0


class BirdDetector:
    """Pre-filter using YOLOv8-nano to confirm a bird is in the frame.

    Only the COCO "bird" class (ID 14) is detected. Returns bounding boxes
    so the worker can crop to the bird region before species classification.
    """

    BIRD_CLASS_ID = 14

    def __init__(self, model_name: str = "yolov8n.pt", confidence: float = 0.4):
        from ultralytics import YOLO
        logger.info("Loading YOLO model: %s (conf=%.2f)", model_name, confidence)
        self.model = YOLO(model_name)
        self.confidence = confidence

    def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        results = self.model(
            frame,
            classes=[self.BIRD_CLASS_ID],
            conf=self.confidence,
            verbose=False,
        )
        boxes = []
        for r in results:
            for box in r.boxes.xyxy.cpu().numpy().astype(int):
                boxes.append((int(box[0]), int(box[1]), int(box[2]), int(box[3])))
        return boxes


def _crop_with_padding(
    frame: np.ndarray,
    box: tuple[int, int, int, int],
    padding: float = 0.15,
) -> np.ndarray:
    """Crop frame to bounding box with proportional padding on each side."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    pad_x = int(bw * padding)
    pad_y = int(bh * padding)
    x1 = max(0, x1 - pad_x)
    y1 = max(0, y1 - pad_y)
    x2 = min(w, x2 + pad_x)
    y2 = min(h, y2 + pad_y)
    return frame[y1:y2, x1:x2]


class FrameRecorder:
    """Buffers JPEG frames during a detection session and flushes them to the catalog.

    A recording session starts when a bird is detected. Frames are captured
    continuously during the cooldown period. Each new detection resets the
    cooldown timer and may start a new session (flushing the old one first).
    When the cooldown expires, the remaining frames are flushed.
    """

    MAX_BUFFER_SIZE = 200

    def __init__(self, catalog_url: str, client: httpx.Client, jpeg_quality: int = 90):
        self._catalog_url = catalog_url
        self._client = client
        self._jpeg_quality = jpeg_quality

        self._detection_id: str | None = None
        self._buffer: list[dict] = []
        self._sequence: int = 0

    @property
    def active(self) -> bool:
        return self._detection_id is not None

    @property
    def detection_id(self) -> str | None:
        return self._detection_id

    def start_session(self, detection_id: str) -> None:
        """Begin a new recording session, flushing any existing one first."""
        if self._detection_id and self._buffer:
            self._flush()
        self._detection_id = detection_id
        self._buffer = []
        self._sequence = 0
        logger.info("Frame recording started for detection %s", detection_id[:8])

    def record_frame(self, frame: np.ndarray, has_bird: bool = False) -> None:
        """Add a frame to the buffer."""
        if not self._detection_id:
            return

        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
        h, w = frame.shape[:2]

        self._buffer.append({
            "sequence_number": self._sequence,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "has_bird": has_bird,
            "jpeg_b64": base64.b64encode(buf.tobytes()).decode("ascii"),
            "frame_width": w,
            "frame_height": h,
        })
        self._sequence += 1

        if len(self._buffer) >= self.MAX_BUFFER_SIZE:
            self._flush()

    def stop_session(self) -> None:
        """End the current session and flush remaining frames."""
        if self._detection_id and self._buffer:
            self._flush()
        if self._detection_id:
            logger.info(
                "Frame recording stopped for detection %s (%d frames total)",
                self._detection_id[:8], self._sequence,
            )
        self._detection_id = None
        self._buffer = []
        self._sequence = 0

    def _flush(self) -> None:
        """Send buffered frames to the catalog API."""
        if not self._detection_id or not self._buffer:
            return

        url = f"{self._catalog_url}/api/v1/detections/{self._detection_id}/frames"
        payload = {
            "detection_id": self._detection_id,
            "frames": self._buffer,
        }

        try:
            resp = self._client.post(url, json=payload, timeout=60.0)
            resp.raise_for_status()
            result = resp.json()
            logger.debug(
                "Flushed %d frames for detection %s",
                result.get("frames_inserted", len(self._buffer)),
                self._detection_id[:8],
            )
        except Exception:
            logger.exception(
                "Failed to flush %d frames for detection %s",
                len(self._buffer), self._detection_id[:8],
            )

        self._buffer = []


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

        motion_cfg = cfg.get("motion", {})
        self.cooldown = float(motion_cfg.get("cooldown_seconds", 5.0))
        self.min_confidence = float(cfg.get("inference", {}).get("min_confidence", 0.10))

        bird_cfg = cfg.get("bird_detector", {})
        self.bird_detector_enabled = bool(bird_cfg.get("enabled", True))
        self.bird_padding = float(bird_cfg.get("padding_fraction", 0.15))
        self.bird_detector: BirdDetector | None = None
        if self.bird_detector_enabled:
            self.bird_detector = BirdDetector(
                model_name=bird_cfg.get("model", "yolov8n.pt"),
                confidence=float(bird_cfg.get("confidence", 0.4)),
            )

        self.motion = MotionDetector(
            threshold=int(cfg["stream"]["motion_threshold"]),
            min_area_fraction=float(motion_cfg.get("min_area_fraction", 0.005)),
            max_area_fraction=float(motion_cfg.get("max_area_fraction", 0.6)),
            min_contour_area=int(motion_cfg.get("min_contour_area", 500)),
        )
        self.client = httpx.Client(timeout=30.0)
        self.recorder = FrameRecorder(self.catalog_url, self.client, self.jpeg_quality)

        self._running = True
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

        self._consecutive_errors = 0
        self._max_errors = 20
        self._last_inference = 0.0

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
        logger.info("  Cooldown: %.1fs between inferences", self.cooldown)
        logger.info("  Min confidence: %.0f%%", self.min_confidence * 100)
        logger.info("  Bird pre-filter: %s", "YOLO enabled" if self.bird_detector else "disabled")
        logger.info("  Frame recording: enabled (captures during cooldown)")

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
                self.recorder.stop_session()
                proc.terminate()
                proc.wait()
                time.sleep(5)
                proc = self._open_stream()
                self.motion = MotionDetector(
                    threshold=self.motion.threshold,
                    min_area_fraction=self.motion.min_area_fraction,
                    max_area_fraction=self.motion.max_area_fraction,
                    min_contour_area=self.motion.min_contour_area,
                )
                continue

            now = time.time()
            frame = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
            has_motion = self.motion.detect(frame)

            # During an active recording session, always capture at motion_fps
            if self.recorder.active:
                current_interval = 1.0 / self.motion_fps
            else:
                current_interval = (1.0 / self.motion_fps) if has_motion else interval

            if now - last_capture < current_interval:
                continue
            last_capture = now

            # If recording is active but cooldown has expired, stop the session
            if self.recorder.active and (now - self._last_inference >= self.cooldown):
                self.recorder.stop_session()

            # If recording is active, always capture the frame (even without motion)
            if self.recorder.active:
                bird_in_frame = False
                if self.bird_detector:
                    boxes = self.bird_detector.detect(frame)
                    bird_in_frame = len(boxes) > 0
                self.recorder.record_frame(frame, has_bird=bird_in_frame)

            if not has_motion:
                continue

            # Cooldown check for inference (not for recording)
            if now - self._last_inference < self.cooldown:
                continue

            frames_processed += 1
            frame_id = str(uuid.uuid4())

            try:
                classify_frame = frame
                if self.bird_detector:
                    boxes = self.bird_detector.detect(frame)
                    if not boxes:
                        logger.debug("YOLO: no bird in frame — skipping")
                        continue
                    largest = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
                    classify_frame = _crop_with_padding(frame, largest, self.bird_padding)
                    logger.debug(
                        "YOLO: bird at (%d,%d)-(%d,%d), crop %dx%d",
                        *largest, classify_frame.shape[1], classify_frame.shape[0],
                    )

                jpeg = self._encode_jpeg(classify_frame)
                t0 = time.perf_counter()
                predictions = self._classify(jpeg)
                inference_ms = (time.perf_counter() - t0) * 1000
                self._last_inference = time.time()

                top_confidence = predictions[0]["confidence"] if predictions else 0
                if top_confidence < self.min_confidence:
                    logger.debug(
                        "Skipped — top confidence %.1f%% below threshold %.0f%%",
                        top_confidence * 100, self.min_confidence * 100,
                    )
                    continue

                validated = self._validate(predictions, frame_id)

                species = validated.get("common_name", "unknown")
                confidence = validated.get("adjusted_confidence", 0)
                status = "validated" if validated.get("ebird_validated") else "unvalidated"
                rerouted = validated.get("was_rerouted", False)
                detection_id = validated.get("detection_id", "")

                # Start (or restart) frame recording for this detection
                self.recorder.start_session(detection_id)
                self.recorder.record_frame(frame, has_bird=True)

                reroute_tag = " [REROUTED]" if rerouted else ""
                logger.info(
                    "Detection #%d | %s (%.1f%%) | %s%s | %.0fms | %s | recording",
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

        self.recorder.stop_session()
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
