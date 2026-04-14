"""SQLAlchemy and Pydantic models for the detections and detection_frames tables."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

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

    frames = relationship("DetectionFrameORM", back_populates="detection", cascade="all, delete-orphan")


class DetectionFrameORM(Base):
    __tablename__ = "detection_frames"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    detection_id = Column(UUID(as_uuid=True), ForeignKey("detections.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence_number = Column(Integer, nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False)
    has_bird = Column(Boolean, default=False)
    jpeg_data = Column(LargeBinary, nullable=False)
    frame_width = Column(Integer, nullable=True)
    frame_height = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    detection = relationship("DetectionORM", back_populates="frames")


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
    frame_count: int | None = None

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


class DetectionFrameResponse(BaseModel):
    id: uuid.UUID
    detection_id: uuid.UUID
    sequence_number: int
    captured_at: datetime
    has_bird: bool
    frame_width: int | None = None
    frame_height: int | None = None

    model_config = {"from_attributes": True}


class DetectionFrameListResponse(BaseModel):
    detection_id: uuid.UUID
    total_frames: int
    frames: list[DetectionFrameResponse]
