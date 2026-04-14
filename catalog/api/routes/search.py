"""Search and analytics endpoints backed by PostgreSQL."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import cast, func, or_, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.api.db import get_db
from catalog.api.models.detection import DetectionORM

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.get("/search")
async def search_detections(
    q: str = Query(..., min_length=1, description="Search query"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Search detections by species name, validation notes, or metadata."""
    like = f"%{q}%"
    filters = or_(
        DetectionORM.validation_notes.ilike(like),
        cast(DetectionORM.extra_metadata, String).ilike(like),
    )

    count_stmt = select(func.count(DetectionORM.id)).where(filters)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(DetectionORM)
        .where(filters)
        .order_by(DetectionORM.detected_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    detections = result.scalars().all()

    items = []
    for d in detections:
        meta = d.extra_metadata or {}
        items.append({
            "detection_id": str(d.id),
            "species": meta.get("common_name", ""),
            "species_code": meta.get("species_code", ""),
            "confidence": d.confidence,
            "detected_at": d.detected_at.isoformat() if d.detected_at else None,
            "ebird_validated": d.ebird_validated,
            "validation_notes": d.validation_notes,
        })

    return {
        "total": total,
        "items": items,
        "page": page,
        "page_size": page_size,
    }


@router.get("/analytics/summary")
async def analytics_summary(db: AsyncSession = Depends(get_db)):
    """Dashboard summary statistics."""
    total_stmt = select(func.count(DetectionORM.id))
    total = (await db.execute(total_stmt)).scalar_one()

    species_stmt = select(func.count(func.distinct(DetectionORM.species_id)))
    species_count = (await db.execute(species_stmt)).scalar_one()

    avg_conf_stmt = select(func.avg(DetectionORM.confidence))
    avg_confidence = (await db.execute(avg_conf_stmt)).scalar_one()

    latest_stmt = select(func.max(DetectionORM.detected_at))
    latest = (await db.execute(latest_stmt)).scalar_one()

    today_stmt = select(func.count(DetectionORM.id)).where(
        func.date_trunc("day", DetectionORM.detected_at) == func.current_date()
    )
    today_count = (await db.execute(today_stmt)).scalar_one()

    return {
        "total_detections": total,
        "unique_species": species_count,
        "average_confidence": round(avg_confidence, 4) if avg_confidence else 0.0,
        "latest_detection": latest.isoformat() if latest else None,
        "detections_today": today_count,
    }
