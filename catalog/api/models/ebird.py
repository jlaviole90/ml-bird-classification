"""SQLAlchemy ORM and Pydantic models for eBird integration tables."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from catalog.api.models.species import Base


# ── ORM Models ──────────────────────────────────────────


class EBirdLocalSpeciesORM(Base):
    __tablename__ = "ebird_local_species"

    species_code = Column(String(10), primary_key=True)
    common_name = Column(Text, nullable=False)
    scientific_name = Column(Text, nullable=True)
    last_observed = Column(Date, nullable=True)
    observation_count = Column(Integer, default=0)
    is_notable = Column(Boolean, default=False)
    region_code = Column(String(20), nullable=False)
    synced_at = Column(DateTime(timezone=True), server_default=func.now())


class EBirdSeasonalFrequencyORM(Base):
    __tablename__ = "ebird_seasonal_frequency"

    species_code = Column(String(10), primary_key=True)
    region_code = Column(String(20), primary_key=True)
    week_number = Column(Integer, primary_key=True)
    frequency = Column(Float, nullable=False)
    sample_size = Column(Integer, nullable=True)


class EBirdNotableSightingORM(Base):
    __tablename__ = "ebird_notable_sightings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    species_code = Column(String(10), nullable=False)
    common_name = Column(Text, nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    location_name = Column(Text, nullable=True)
    how_many = Column(Integer, nullable=True)
    valid = Column(Boolean, default=True)
    synced_at = Column(DateTime(timezone=True), server_default=func.now())


class EBirdHotspotORM(Base):
    __tablename__ = "ebird_hotspots"

    hotspot_id = Column(String(20), primary_key=True)
    name = Column(Text, nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    country_code = Column(String(5), nullable=True)
    subnational1 = Column(String(10), nullable=True)
    latest_obs_date = Column(Date, nullable=True)
    num_species = Column(Integer, default=0)
    synced_at = Column(DateTime(timezone=True), server_default=func.now())


class YardLifeListORM(Base):
    __tablename__ = "yard_life_list"

    id = Column(Integer, primary_key=True, autoincrement=True)
    species_id = Column(Integer, ForeignKey("species.id"), nullable=True)
    species_code = Column(String(10), unique=True, nullable=False)
    first_detected_at = Column(DateTime(timezone=True), nullable=False)
    last_detected_at = Column(DateTime(timezone=True), nullable=False)
    total_detections = Column(Integer, default=1)
    best_confidence = Column(Float, nullable=True)
    best_frame_s3_key = Column(Text, nullable=True)
    ebird_confirmed = Column(Boolean, default=False)


class IdentificationAuditLogORM(Base):
    __tablename__ = "identification_audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    detection_id = Column(UUID(as_uuid=True), ForeignKey("detections.id", ondelete="CASCADE"), nullable=True)
    frame_id = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    model_name = Column(Text, nullable=False)
    inference_latency_ms = Column(Float, nullable=True)
    candidates = Column(JSONB, nullable=False)

    ebird_region = Column(Text, nullable=True)
    ebird_week = Column(Integer, nullable=True)
    local_list_size = Column(Integer, nullable=True)
    local_list_synced_at = Column(DateTime(timezone=True), nullable=True)

    accepted_rank = Column(Integer, nullable=True)
    accepted_species_code = Column(Text, nullable=True)
    final_confidence = Column(Float, nullable=True)
    was_rerouted = Column(Boolean, default=False)
    is_notable = Column(Boolean, default=False)
    decision_time_ms = Column(Float, nullable=True)

    summary = Column(Text, nullable=True)
    pipeline_version = Column(Text, nullable=True)


# ── Pydantic Schemas ────────────────────────────────────


class LocalSpeciesResponse(BaseModel):
    species_code: str
    common_name: str
    scientific_name: str | None = None
    last_observed: date | None = None
    observation_count: int = 0
    is_notable: bool = False
    current_week_frequency: float | None = None

    model_config = {"from_attributes": True}


class NotableSightingResponse(BaseModel):
    id: int
    species_code: str
    common_name: str
    observed_at: datetime
    lat: float | None = None
    lng: float | None = None
    location_name: str | None = None
    how_many: int | None = None

    model_config = {"from_attributes": True}


class HotspotResponse(BaseModel):
    hotspot_id: str
    name: str
    lat: float
    lng: float
    latest_obs_date: date | None = None
    num_species: int = 0

    model_config = {"from_attributes": True}


class YardLifeListEntry(BaseModel):
    id: int
    species_code: str
    species_id: int | None = None
    first_detected_at: datetime
    last_detected_at: datetime
    total_detections: int
    best_confidence: float | None = None
    best_frame_s3_key: str | None = None
    ebird_confirmed: bool = False

    model_config = {"from_attributes": True}


class YardListStats(BaseModel):
    total_species: int
    total_detections: int
    ebird_confirmed_count: int
    local_list_size: int
    coverage_pct: float
    latest_new_species: str | None = None
    latest_new_species_date: datetime | None = None


class CandidateEvalSchema(BaseModel):
    rank: int
    species_code: str
    common_name: str
    raw_confidence: float
    on_local_list: bool | None = None
    seasonal_frequency: float | None = None
    adjusted_confidence: float | None = None
    rejection_reason: str | None = None


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    detection_id: uuid.UUID | None = None
    frame_id: str
    created_at: datetime
    model_name: str
    inference_latency_ms: float | None = None
    candidates: list[CandidateEvalSchema]
    ebird_region: str | None = None
    ebird_week: int | None = None
    local_list_size: int | None = None
    accepted_rank: int | None = None
    accepted_species_code: str | None = None
    final_confidence: float | None = None
    was_rerouted: bool = False
    is_notable: bool = False
    decision_time_ms: float | None = None
    summary: str | None = None
    pipeline_version: str | None = None

    model_config = {"from_attributes": True}


class AuditStatsResponse(BaseModel):
    total_decisions: int
    rerouted_count: int
    reroute_rate: float
    rejected_count: int
    rejection_rate: float
    avg_decision_time_ms: float | None = None
    top_rejection_reasons: list[dict[str, Any]]
    top_reroute_pairs: list[dict[str, Any]]
