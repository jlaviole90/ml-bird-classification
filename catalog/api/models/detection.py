"""SQLAlchemy and Pydantic models for the detections table."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from catalog.api.models.species import Base


class DetectionORM(Base):
    __tablename__ = "detections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    species_id = Column(Integer, ForeignKey("species.id"), nullable=True)
    confidence = Column(Float, nullable=False)
    frame_s3_key = Column(Text, nullable=False)
    source_camera = Column(String(64), default="birdcam-01")
    detected_at = Column(DateTime(timezone=True), nullable=False)
    bounding_box = Column(JSONB, nullable=True)
    extra_metadata = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    raw_confidence = Column(Float, nullable=True)
    ebird_frequency = Column(Float, nullable=True)
    ebird_validated = Column(Boolean, default=False)
    validation_notes = Column(Text, nullable=True)


# ── Pydantic schemas ────────────────────────────────────

class DetectionResponse(BaseModel):
    id: uuid.UUID
    species_id: int | None = None
    confidence: float
    frame_s3_key: str
    frame_url: str | None = None
    source_camera: str
    detected_at: datetime
    bounding_box: dict[str, Any] | None = None
    extra_metadata: dict[str, Any] | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class DetectionCreate(BaseModel):
    species_id: int | None = None
    confidence: float
    frame_s3_key: str
    source_camera: str = "birdcam-01"
    detected_at: datetime
    bounding_box: dict[str, Any] | None = None
    extra_metadata: dict[str, Any] | None = None


class DetectionListResponse(BaseModel):
    items: list[DetectionResponse]
    total: int
    page: int
    page_size: int
