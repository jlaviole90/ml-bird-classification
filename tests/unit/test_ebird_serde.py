"""Unit tests for DetectionResult with eBird fields."""

from __future__ import annotations

import json

from pipeline.flink.src.serde import DetectionResult


class TestDetectionResultEBirdFields:
    def test_ebird_fields_default(self):
        det = DetectionResult(
            detection_id="det-1",
            frame_id="frm-1",
            timestamp="2026-04-13T00:00:00Z",
            source="birdcam-01",
            species="American Robin",
            class_id=42,
            confidence=0.95,
            top5=[],
            s3_key="frames/test.jpg",
            resolution="1280x720",
        )
        assert det.ebird_validated is False
        assert det.is_notable is False
        assert det.was_rerouted is False
        assert det.raw_confidence is None
        assert det.audit_id == ""

    def test_ebird_fields_populated(self):
        det = DetectionResult(
            detection_id="det-2",
            frame_id="frm-2",
            timestamp="2026-04-13T00:00:00Z",
            source="birdcam-01",
            species="House Sparrow",
            class_id=10,
            confidence=0.88,
            top5=[],
            s3_key="frames/test.jpg",
            resolution="1280x720",
            raw_confidence=0.92,
            adjusted_confidence=0.88,
            ebird_validated=True,
            ebird_frequency=0.75,
            is_notable=False,
            was_rerouted=True,
            validation_notes="Rerouted from rank-1 Exotic Bird",
            audit_id="abc-123",
        )
        data = json.loads(det.to_json_bytes())
        assert data["ebird_validated"] is True
        assert data["was_rerouted"] is True
        assert data["raw_confidence"] == 0.92
        assert data["adjusted_confidence"] == 0.88
        assert data["audit_id"] == "abc-123"
        assert data["validation_notes"] == "Rerouted from rank-1 Exotic Bird"
