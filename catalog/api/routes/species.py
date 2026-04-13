"""Species endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.api.db import get_db
from catalog.api.models.detection import DetectionORM
from catalog.api.models.species import SpeciesORM, SpeciesResponse, SpeciesWithCount

router = APIRouter(prefix="/api/v1/species", tags=["species"])


@router.get("", response_model=list[SpeciesWithCount])
async def list_species(db: AsyncSession = Depends(get_db)):
    """List all species with their detection counts, ordered by count descending."""
    stmt = (
        select(
            SpeciesORM,
            func.count(DetectionORM.id).label("detection_count"),
        )
        .outerjoin(DetectionORM, DetectionORM.species_id == SpeciesORM.id)
        .group_by(SpeciesORM.id)
        .order_by(func.count(DetectionORM.id).desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    return [
        SpeciesWithCount(
            **SpeciesResponse.model_validate(row[0]).model_dump(),
            detection_count=row[1],
        )
        for row in rows
    ]


@router.get("/{species_id}", response_model=SpeciesResponse)
async def get_species(species_id: int, db: AsyncSession = Depends(get_db)):
    species = await db.get(SpeciesORM, species_id)
    if not species:
        raise HTTPException(status_code=404, detail="Species not found")
    return species


@router.get("/{species_id}/timeline")
async def species_timeline(species_id: int, db: AsyncSession = Depends(get_db)):
    """Detection frequency over time for a given species (daily buckets)."""
    species = await db.get(SpeciesORM, species_id)
    if not species:
        raise HTTPException(status_code=404, detail="Species not found")

    stmt = (
        select(
            func.date_trunc("day", DetectionORM.detected_at).label("date"),
            func.count().label("count"),
            func.avg(DetectionORM.confidence).label("avg_confidence"),
        )
        .where(DetectionORM.species_id == species_id)
        .group_by("date")
        .order_by("date")
    )
    result = await db.execute(stmt)
    rows = result.all()

    return {
        "species": SpeciesResponse.model_validate(species).model_dump(),
        "timeline": [
            {"date": str(r.date.date()), "count": r.count, "avg_confidence": round(r.avg_confidence, 4)}
            for r in rows
        ],
    }
