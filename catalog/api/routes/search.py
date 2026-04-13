"""Elasticsearch-backed search and analytics endpoints."""

from __future__ import annotations

import os

from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.api.db import get_db
from catalog.api.models.detection import DetectionORM

router = APIRouter(prefix="/api/v1", tags=["search"])

ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
ES_INDEX = "bird-detections"


def _get_es() -> AsyncElasticsearch:
    return AsyncElasticsearch(ES_URL)


@router.get("/search")
async def search_detections(
    q: str = Query(..., min_length=1, description="Search query"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Full-text search across species names, metadata, and camera sources."""
    es = _get_es()
    try:
        body = {
            "query": {
                "multi_match": {
                    "query": q,
                    "fields": ["species^3", "source", "metadata.*"],
                    "fuzziness": "AUTO",
                }
            },
            "from": (page - 1) * page_size,
            "size": page_size,
            "sort": [{"detected_at": {"order": "desc"}}],
        }
        result = await es.search(index=ES_INDEX, body=body)
        hits = result["hits"]
        return {
            "total": hits["total"]["value"],
            "items": [hit["_source"] for hit in hits["hits"]],
            "page": page,
            "page_size": page_size,
        }
    finally:
        await es.close()


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
