"""FastAPI routes for eBird integration, yard life list, and audit log queries."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, cast, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.api.db import get_db
from catalog.api.ebird.audit import CandidateEval, DecisionTrace, write_audit_log
from catalog.api.ebird.sync import get_ebird_week_number
from catalog.api.ebird.validator import EBirdValidator, Prediction
from catalog.api.models.detection import DetectionORM
from catalog.api.models.ebird import (
    AuditLogResponse,
    AuditStatsResponse,
    CandidateEvalSchema,
    EBirdHotspotORM,
    EBirdLocalSpeciesORM,
    EBirdNotableSightingORM,
    EBirdSeasonalFrequencyORM,
    HotspotResponse,
    IdentificationAuditLogORM,
    LocalSpeciesResponse,
    NotableSightingResponse,
    YardLifeListEntry,
    YardLifeListORM,
    YardListStats,
)

router = APIRouter(prefix="/api/v1", tags=["ebird"])


# ── eBird Data Endpoints ────────────────────────────────


@router.get("/ebird/local-species", response_model=list[LocalSpeciesResponse])
async def list_local_species(db: AsyncSession = Depends(get_db)):
    """Current local species list with seasonal frequency for the current week."""
    week = get_ebird_week_number()
    result = await db.execute(select(EBirdLocalSpeciesORM))
    species = result.scalars().all()

    out = []
    for sp in species:
        freq_result = await db.execute(
            select(EBirdSeasonalFrequencyORM.frequency).where(
                EBirdSeasonalFrequencyORM.species_code == sp.species_code,
                EBirdSeasonalFrequencyORM.week_number == week,
            )
        )
        freq_row = freq_result.first()

        out.append(LocalSpeciesResponse(
            species_code=sp.species_code,
            common_name=sp.common_name,
            scientific_name=sp.scientific_name,
            last_observed=sp.last_observed,
            observation_count=sp.observation_count,
            is_notable=sp.is_notable,
            current_week_frequency=freq_row[0] if freq_row else None,
        ))
    return out


@router.get("/ebird/notable", response_model=list[NotableSightingResponse])
async def list_notable_sightings(
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Recent notable (rare) sightings in the area."""
    result = await db.execute(
        select(EBirdNotableSightingORM)
        .order_by(desc(EBirdNotableSightingORM.observed_at))
        .limit(limit)
    )
    return [NotableSightingResponse.model_validate(s) for s in result.scalars().all()]


@router.get("/ebird/hotspots", response_model=list[HotspotResponse])
async def list_hotspots(db: AsyncSession = Depends(get_db)):
    """Nearby hotspots with activity counts."""
    result = await db.execute(
        select(EBirdHotspotORM).order_by(desc(EBirdHotspotORM.num_species))
    )
    return [HotspotResponse.model_validate(h) for h in result.scalars().all()]


# ── Yard Life List ──────────────────────────────────────


@router.get("/yard-list", response_model=list[YardLifeListEntry])
async def get_yard_list(db: AsyncSession = Depends(get_db)):
    """Cumulative yard life list with first/last detection, count, best photo."""
    result = await db.execute(
        select(YardLifeListORM).order_by(desc(YardLifeListORM.last_detected_at))
    )
    return [YardLifeListEntry.model_validate(y) for y in result.scalars().all()]


@router.get("/yard-list/stats", response_model=YardListStats)
async def get_yard_list_stats(db: AsyncSession = Depends(get_db)):
    """Summary: total species, confirmed count, coverage vs local list."""
    yard_result = await db.execute(select(YardLifeListORM))
    yard_entries = yard_result.scalars().all()

    local_count_result = await db.execute(select(func.count()).select_from(EBirdLocalSpeciesORM))
    local_list_size = local_count_result.scalar() or 0

    total = len(yard_entries)
    confirmed = sum(1 for y in yard_entries if y.ebird_confirmed)
    total_detections = sum(y.total_detections for y in yard_entries)
    coverage = (total / local_list_size * 100) if local_list_size > 0 else 0.0

    latest = None
    latest_date = None
    if yard_entries:
        newest = max(yard_entries, key=lambda y: y.first_detected_at)
        latest = newest.species_code
        latest_date = newest.first_detected_at

    return YardListStats(
        total_species=total,
        total_detections=total_detections,
        ebird_confirmed_count=confirmed,
        local_list_size=local_list_size,
        coverage_pct=round(coverage, 1),
        latest_new_species=latest,
        latest_new_species_date=latest_date,
    )


# ── Species Migration Timeline ──────────────────────────


@router.get("/species/{species_id}/migration")
async def get_species_migration(
    species_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Detection timeline overlaid with eBird seasonal frequency for migration visualization."""
    from catalog.api.models.species import SpeciesORM

    sp_result = await db.execute(select(SpeciesORM).where(SpeciesORM.id == species_id))
    sp = sp_result.scalar_one_or_none()
    if not sp:
        raise HTTPException(status_code=404, detail="Species not found")

    freq_result = await db.execute(
        select(EBirdSeasonalFrequencyORM).where(
            EBirdSeasonalFrequencyORM.species_code == sp.species_code
        ).order_by(EBirdSeasonalFrequencyORM.week_number)
    )
    frequencies = [
        {"week": f.week_number, "frequency": f.frequency}
        for f in freq_result.scalars().all()
    ]

    det_result = await db.execute(
        select(
            func.date_trunc("week", DetectionORM.detected_at).label("week"),
            func.count().label("count"),
            func.avg(DetectionORM.confidence).label("avg_confidence"),
        )
        .where(DetectionORM.species_id == species_id)
        .group_by(text("1"))
        .order_by(text("1"))
    )
    detections = [
        {"week": str(row.week), "count": row.count, "avg_confidence": float(row.avg_confidence or 0)}
        for row in det_result.all()
    ]

    return {
        "species_id": species_id,
        "species_code": sp.species_code,
        "common_name": sp.common_name,
        "ebird_frequency": frequencies,
        "detections_by_week": detections,
    }


# ── Audit Log Endpoints ────────────────────────────────


@router.get("/detections/{detection_id}/audit", response_model=AuditLogResponse)
async def get_detection_audit(
    detection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Full audit trail for a single detection."""
    result = await db.execute(
        select(IdentificationAuditLogORM).where(
            IdentificationAuditLogORM.detection_id == detection_id
        )
    )
    audit = result.scalar_one_or_none()
    if not audit:
        raise HTTPException(status_code=404, detail="Audit log not found for this detection")
    return AuditLogResponse.model_validate(audit)


@router.get("/audit/rerouted", response_model=list[AuditLogResponse])
async def list_rerouted(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Detections where the model's top-1 pick was overridden by eBird validation."""
    offset = (page - 1) * page_size
    result = await db.execute(
        select(IdentificationAuditLogORM)
        .where(IdentificationAuditLogORM.was_rerouted == True)  # noqa: E712
        .order_by(desc(IdentificationAuditLogORM.created_at))
        .offset(offset)
        .limit(page_size)
    )
    return [AuditLogResponse.model_validate(a) for a in result.scalars().all()]


@router.get("/audit/rejected", response_model=list[AuditLogResponse])
async def list_rejected(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Detections where no candidate passed validation."""
    offset = (page - 1) * page_size
    result = await db.execute(
        select(IdentificationAuditLogORM)
        .where(IdentificationAuditLogORM.accepted_rank == 0)
        .order_by(desc(IdentificationAuditLogORM.created_at))
        .offset(offset)
        .limit(page_size)
    )
    return [AuditLogResponse.model_validate(a) for a in result.scalars().all()]


@router.get("/audit/stats", response_model=AuditStatsResponse)
async def get_audit_stats(db: AsyncSession = Depends(get_db)):
    """Aggregate audit metrics: reroute rate, rejection rate, common reroute pairs."""
    total_result = await db.execute(
        select(func.count()).select_from(IdentificationAuditLogORM)
    )
    total = total_result.scalar() or 0

    rerouted_result = await db.execute(
        select(func.count()).select_from(IdentificationAuditLogORM).where(
            IdentificationAuditLogORM.was_rerouted == True  # noqa: E712
        )
    )
    rerouted = rerouted_result.scalar() or 0

    rejected_result = await db.execute(
        select(func.count()).select_from(IdentificationAuditLogORM).where(
            IdentificationAuditLogORM.accepted_rank == 0
        )
    )
    rejected = rejected_result.scalar() or 0

    avg_time_result = await db.execute(
        select(func.avg(IdentificationAuditLogORM.decision_time_ms))
    )
    avg_time = avg_time_result.scalar()

    return AuditStatsResponse(
        total_decisions=total,
        rerouted_count=rerouted,
        reroute_rate=round(rerouted / total, 4) if total > 0 else 0.0,
        rejected_count=rejected,
        rejection_rate=round(rejected / total, 4) if total > 0 else 0.0,
        avg_decision_time_ms=round(avg_time, 2) if avg_time else None,
        top_rejection_reasons=[],
        top_reroute_pairs=[],
    )


# ── Validate Endpoint (called by Flink) ────────────────


class ValidateRequest(BaseModel):
    detection_id: str | None = None
    frame_id: str
    predictions: list[dict[str, Any]]
    inference_latency_ms: float = 0.0


@router.post("/validate")
async def validate_detection(
    req: ValidateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Validate a set of TorchServe predictions against eBird data.

    Called by the Flink inference job before producing to enriched-metadata.
    """
    preds = [
        Prediction(
            rank=i + 1,
            species_code=p.get("species_code", p.get("species", "")),
            common_name=p.get("species", ""),
            confidence=p.get("confidence", 0.0),
        )
        for i, p in enumerate(req.predictions[:5])
    ]

    validator = EBirdValidator(session=db)
    result = validator.validate(
        predictions=preds,
        frame_id=req.frame_id,
        detection_id=req.detection_id or "",
        inference_latency_ms=req.inference_latency_ms,
    )

    vr = await result
    await db.commit()

    return {
        "species_code": vr.species_code,
        "common_name": vr.common_name,
        "raw_confidence": vr.raw_confidence,
        "adjusted_confidence": vr.adjusted_confidence,
        "ebird_frequency": vr.ebird_frequency,
        "ebird_validated": vr.ebird_validated,
        "was_rerouted": vr.was_rerouted,
        "is_notable": vr.is_notable,
        "validation_notes": vr.validation_notes,
        "audit_id": str(vr.audit_id) if vr.audit_id else "",
    }
