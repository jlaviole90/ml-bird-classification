"""Kafka serialization/deserialization for Flink pipeline messages."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class FrameMessage:
    """Deserialized message from the raw-frames Kafka topic."""

    frame_id: str
    timestamp: str
    source: str
    resolution: str
    motion_detected: bool
    frame_bytes: bytes
    s3_key: str = ""

    @classmethod
    def from_kafka(cls, value: bytes, headers: list[tuple[str, bytes]] | None) -> FrameMessage:
        meta: dict[str, Any] = {}
        if headers:
            for key, val in headers:
                if key == "metadata" and val:
                    meta = json.loads(val.decode())
                    break

        return cls(
            frame_id=meta.get("frame_id", ""),
            timestamp=meta.get("timestamp", ""),
            source=meta.get("source", ""),
            resolution=meta.get("resolution", ""),
            motion_detected=meta.get("motion_detected", False),
            frame_bytes=value,
            s3_key=meta.get("s3_key", ""),
        )


@dataclass
class DetectionResult:
    """Enriched detection record produced to the metadata topic."""

    detection_id: str
    frame_id: str
    timestamp: str
    source: str
    species: str
    class_id: int
    confidence: float
    top5: list[dict[str, Any]]
    s3_key: str
    resolution: str
    raw_confidence: float | None = None
    adjusted_confidence: float | None = None
    ebird_validated: bool = False
    ebird_frequency: float | None = None
    is_notable: bool = False
    was_rerouted: bool = False
    validation_notes: str = ""
    audit_id: str = ""

    def to_json_bytes(self) -> bytes:
        return json.dumps(asdict(self)).encode()
