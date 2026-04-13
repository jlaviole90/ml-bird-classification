"""Detection endpoints."""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import boto3
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.api.db import get_db
from catalog.api.models.detection import (
    DetectionCreate,
    DetectionListResponse,
    DetectionORM,
    DetectionResponse,
)

router = APIRouter(prefix="/api/v1/detections", tags=["detections"])

S3_ENDPOINT = os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000")
S3_BUCKET = os.environ.get("S3_BUCKET_RAW", "bird-raw-frames")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "minioadmin")
PRESIGNED_EXPIRY = int(os.environ.get("S3_PRESIGNED_EXPIRY", "3600"))


def _get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )


def _presign(s3_key: str) -> str:
    s3 = _get_s3_client()
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": s3_key},
        ExpiresIn=PRESIGNED_EXPIRY,
    )


@router.get("", response_model=DetectionListResponse)
async def list_detections(
    species_id: int | None = None,
    min_confidence: float | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of detections with optional filters."""
    stmt = select(DetectionORM)
    count_stmt = select(func.count(DetectionORM.id))

    if species_id is not None:
        stmt = stmt.where(DetectionORM.species_id == species_id)
        count_stmt = count_stmt.where(DetectionORM.species_id == species_id)
    if min_confidence is not None:
        stmt = stmt.where(DetectionORM.confidence >= min_confidence)
        count_stmt = count_stmt.where(DetectionORM.confidence >= min_confidence)
    if start is not None:
        stmt = stmt.where(DetectionORM.detected_at >= start)
        count_stmt = count_stmt.where(DetectionORM.detected_at >= start)
    if end is not None:
        stmt = stmt.where(DetectionORM.detected_at <= end)
        count_stmt = count_stmt.where(DetectionORM.detected_at <= end)

    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        stmt
        .order_by(DetectionORM.detected_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    detections = result.scalars().all()

    items = []
    for d in detections:
        resp = DetectionResponse.model_validate(d)
        resp.frame_url = _presign(d.frame_s3_key)
        items.append(resp)

    return DetectionListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{detection_id}", response_model=DetectionResponse)
async def get_detection(detection_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    detection = await db.get(DetectionORM, detection_id)
    if not detection:
        raise HTTPException(status_code=404, detail="Detection not found")
    resp = DetectionResponse.model_validate(detection)
    resp.frame_url = _presign(detection.frame_s3_key)
    return resp


@router.post("", response_model=DetectionResponse, status_code=201)
async def create_detection(body: DetectionCreate, db: AsyncSession = Depends(get_db)):
    detection = DetectionORM(**body.model_dump())
    db.add(detection)
    await db.commit()
    await db.refresh(detection)
    return DetectionResponse.model_validate(detection)
