"""HLS frame extractor with adaptive motion-based sampling.

Connects to a live HLS stream, extracts frames via ffmpeg, detects motion to
increase capture rate when a bird is present, and publishes frames to Kafka and
S3/MinIO for downstream processing.
"""

from __future__ import annotations

import io
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

import boto3
import cv2
import numpy as np
from confluent_kafka import Producer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _resolve_env(val: str) -> str:
    """Expand ${VAR:-default} patterns in config strings."""
    if isinstance(val, str) and val.startswith("${"):
        inner = val[2:-1]
        var, _, default = inner.partition(":-")
        return os.environ.get(var, default)
    return val


def load_config(path: str | Path = "pipeline/ingestion/config.yaml") -> dict[str, Any]:
    import yaml

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


class FrameExtractor:
    """Pull frames from an HLS stream using ffmpeg."""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.stream_url = cfg["stream"]["url"]
        self.base_fps = float(cfg["extraction"]["base_fps"])
        self.motion_fps = float(cfg["extraction"]["motion_fps"])
        self.jpeg_quality = int(cfg["extraction"]["jpeg_quality"])
        self.max_dim = int(cfg["extraction"]["max_dimension"])
        self.source_id = cfg["source_id"]

        self.motion = MotionDetector(threshold=int(cfg["extraction"]["motion_threshold"]))

        self.producer = Producer({"bootstrap.servers": cfg["kafka"]["bootstrap_servers"]})
        self.topic = cfg["kafka"]["topic"]

        s3_cfg = cfg["s3"]
        self.s3 = boto3.client(
            "s3",
            endpoint_url=s3_cfg["endpoint_url"],
            aws_access_key_id=s3_cfg["access_key"],
            aws_secret_access_key=s3_cfg["secret_key"],
        )
        self.bucket = s3_cfg["bucket"]

        self._running = True
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

    def _stop(self, *_: Any) -> None:
        logger.info("Shutdown signal received")
        self._running = False

    def _open_stream(self) -> subprocess.Popen:
        """Open an ffmpeg process that decodes the HLS stream to raw BGR frames on stdout."""
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-i", self.stream_url,
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-vf", f"scale='min({self.max_dim},iw)':'min({self.max_dim},ih)':force_original_aspect_ratio=decrease",
            "-an", "-sn", "pipe:1",
        ]
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10**8)

    def _encode_jpeg(self, frame: np.ndarray) -> bytes:
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        return buf.tobytes()

    def _publish(self, frame_bytes: bytes, metadata: dict) -> None:
        key = metadata["frame_id"].encode()
        self.producer.produce(
            self.topic,
            value=frame_bytes,
            key=key,
            headers={"metadata": json.dumps(metadata).encode()},
        )
        self.producer.poll(0)

        s3_key = f"frames/{metadata['timestamp'][:10]}/{metadata['frame_id']}.jpg"
        self.s3.put_object(Bucket=self.bucket, Key=s3_key, Body=frame_bytes)
        metadata["s3_key"] = s3_key

    def run(self) -> None:
        logger.info("Starting frame extraction from %s", self.stream_url)
        proc = self._open_stream()

        # We need to know the frame dimensions — read two probe bytes by trying one frame
        # ffmpeg -i <url> with rawvideo output; we'll use a fixed expected resolution
        # and re-open if needed. For robustness we probe with ffprobe first.
        width, height = self._probe_resolution()
        frame_size = width * height * 3
        logger.info("Stream resolution: %dx%d (%d bytes/frame)", width, height, frame_size)

        interval = 1.0 / self.base_fps
        last_capture = 0.0

        while self._running:
            raw = proc.stdout.read(frame_size)
            if len(raw) < frame_size:
                logger.warning("Stream ended or read error — reconnecting in 5s")
                proc.terminate()
                time.sleep(5)
                proc = self._open_stream()
                continue

            now = time.time()
            frame = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
            has_motion = self.motion.detect(frame)

            current_interval = (1.0 / self.motion_fps) if has_motion else interval
            if now - last_capture < current_interval:
                continue
            last_capture = now

            frame_bytes = self._encode_jpeg(frame)
            ts = datetime.now(timezone.utc).isoformat()
            metadata = {
                "frame_id": str(uuid.uuid4()),
                "timestamp": ts,
                "source": self.source_id,
                "resolution": f"{width}x{height}",
                "motion_detected": has_motion,
            }

            self._publish(frame_bytes, metadata)
            logger.info(
                "Frame %s  motion=%s  size=%dKB",
                metadata["frame_id"][:8], has_motion, len(frame_bytes) // 1024,
            )

        proc.terminate()
        self.producer.flush()
        logger.info("Extractor stopped")

    def _probe_resolution(self) -> tuple[int, int]:
        """Use ffprobe to get stream resolution."""
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            self.stream_url,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            info = json.loads(result.stdout)
            stream = info["streams"][0]
            w = int(stream["width"])
            h = int(stream["height"])
            return min(w, self.max_dim), min(h, self.max_dim)
        except Exception:
            logger.warning("ffprobe failed — falling back to 1280x720")
            return 1280, 720


def main() -> None:
    cfg = load_config()
    extractor = FrameExtractor(cfg)
    extractor.run()


if __name__ == "__main__":
    main()
