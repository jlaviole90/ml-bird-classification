"""Unit tests for Flink pipeline serialization."""

from __future__ import annotations

import json

from pipeline.flink.src.serde import DetectionResult, FrameMessage


class TestFrameMessage:
    def test_from_kafka_with_metadata(self):
        meta = {
            "frame_id": "abc-123",
            "timestamp": "2024-01-01T00:00:00Z",
            "source": "birdcam-01",
            "resolution": "1280x720",
            "motion_detected": True,
            "s3_key": "frames/2024-01-01/abc-123.jpg",
        }
        headers = [("metadata", json.dumps(meta).encode())]
        msg = FrameMessage.from_kafka(b"\xff\xd8\xff", headers)
        assert msg.frame_id == "abc-123"
        assert msg.motion_detected is True
        assert msg.s3_key == "frames/2024-01-01/abc-123.jpg"

    def test_from_kafka_no_headers(self):
        msg = FrameMessage.from_kafka(b"data", None)
        assert msg.frame_id == ""


class TestDetectionResult:
    def test_to_json_roundtrip(self):
        det = DetectionResult(
            detection_id="det-1",
            frame_id="frm-1",
            timestamp="2024-01-01T00:00:00Z",
            source="birdcam-01",
            species="American Robin",
            class_id=42,
            confidence=0.95,
            top5=[{"species": "American Robin", "class_id": 42, "confidence": 0.95}],
            s3_key="frames/test.jpg",
            resolution="1280x720",
        )
        data = json.loads(det.to_json_bytes())
        assert data["species"] == "American Robin"
        assert data["confidence"] == 0.95
