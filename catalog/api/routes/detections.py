"""Detection and detection frame endpoints."""

from __future__ import annotations

import base64
import logging
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from catalog.api.db import get_db
from catalog.api.models.detection import (
    DetectionCreate,
    DetectionFrameListResponse,
    DetectionFrameORM,
    DetectionFrameResponse,
    DetectionListResponse,
    DetectionORM,
    DetectionResponse,
)

router = APIRouter(prefix="/api/v1/detections", tags=["detections"])


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
        resp.frame_url = None
        fc_result = await db.execute(
            select(func.count(DetectionFrameORM.id)).where(DetectionFrameORM.detection_id == d.id)
        )
        resp.frame_count = fc_result.scalar_one()
        items.append(resp)

    return DetectionListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{detection_id}", response_model=DetectionResponse)
async def get_detection(detection_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    detection = await db.get(DetectionORM, detection_id)
    if not detection:
        raise HTTPException(status_code=404, detail="Detection not found")
    resp = DetectionResponse.model_validate(detection)
    resp.frame_url = None
    fc_result = await db.execute(
        select(func.count(DetectionFrameORM.id)).where(DetectionFrameORM.detection_id == detection_id)
    )
    resp.frame_count = fc_result.scalar_one()
    return resp


@router.post("", response_model=DetectionResponse, status_code=201)
async def create_detection(body: DetectionCreate, db: AsyncSession = Depends(get_db)):
    detection = DetectionORM(**body.model_dump())
    db.add(detection)
    await db.commit()
    await db.refresh(detection)
    return DetectionResponse.model_validate(detection)


# ── Detection Frames ─────────────────────────────────────


class FrameUpload(BaseModel):
    sequence_number: int
    captured_at: datetime
    has_bird: bool = False
    jpeg_b64: str
    frame_width: int | None = None
    frame_height: int | None = None


class FrameBatchUpload(BaseModel):
    detection_id: uuid.UUID
    frames: list[FrameUpload]


@router.post("/{detection_id}/frames", status_code=201)
async def upload_frames(
    detection_id: uuid.UUID,
    body: FrameBatchUpload,
    db: AsyncSession = Depends(get_db),
):
    """Upload a batch of JPEG frames for a detection session."""
    detection = await db.get(DetectionORM, detection_id)
    if not detection:
        raise HTTPException(status_code=404, detail="Detection not found")

    inserted = 0
    for f in body.frames:
        jpeg_data = base64.b64decode(f.jpeg_b64)
        frame_orm = DetectionFrameORM(
            detection_id=detection_id,
            sequence_number=f.sequence_number,
            captured_at=f.captured_at,
            has_bird=f.has_bird,
            jpeg_data=jpeg_data,
            frame_width=f.frame_width,
            frame_height=f.frame_height,
        )
        db.add(frame_orm)
        inserted += 1

    await db.commit()
    return {"detection_id": str(detection_id), "frames_inserted": inserted}


@router.get("/{detection_id}/frames", response_model=DetectionFrameListResponse)
async def list_frames(
    detection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """List all frames for a detection (metadata only, no image data)."""
    detection = await db.get(DetectionORM, detection_id)
    if not detection:
        raise HTTPException(status_code=404, detail="Detection not found")

    result = await db.execute(
        select(DetectionFrameORM)
        .where(DetectionFrameORM.detection_id == detection_id)
        .order_by(DetectionFrameORM.sequence_number)
    )
    frames = result.scalars().all()

    return DetectionFrameListResponse(
        detection_id=detection_id,
        total_frames=len(frames),
        frames=[DetectionFrameResponse.model_validate(f) for f in frames],
    )


@router.get("/{detection_id}/frames/{frame_id}/image")
async def get_frame_image(
    detection_id: uuid.UUID,
    frame_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return raw JPEG image for a specific frame."""
    result = await db.execute(
        select(DetectionFrameORM).where(
            DetectionFrameORM.id == frame_id,
            DetectionFrameORM.detection_id == detection_id,
        )
    )
    frame = result.scalar_one_or_none()
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")

    return Response(content=frame.jpeg_data, media_type="image/jpeg")


# ── Video Assembly ───────────────────────────────────────


@router.get("/{detection_id}/video")
async def get_detection_video(
    detection_id: uuid.UUID,
    fps: int = Query(3, ge=1, le=30, description="Output video frame rate"),
    db: AsyncSession = Depends(get_db),
):
    """Assemble stored frames into an MP4 video and return it.

    Frames are piped through FFmpeg to produce an H.264 MP4 that plays
    natively in browsers.  The `fps` parameter controls playback speed.
    """
    detection = await db.get(DetectionORM, detection_id)
    if not detection:
        raise HTTPException(status_code=404, detail="Detection not found")

    result = await db.execute(
        select(DetectionFrameORM)
        .where(DetectionFrameORM.detection_id == detection_id)
        .order_by(DetectionFrameORM.sequence_number)
    )
    frames = result.scalars().all()

    if not frames:
        raise HTTPException(status_code=404, detail="No frames recorded for this detection")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        for i, f in enumerate(frames):
            (tmp / f"frame_{i:06d}.jpg").write_bytes(f.jpeg_data)

        output_path = tmp / "output.mp4"

        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", str(tmp / "frame_%06d.jpg"),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-preset", "fast",
            "-crf", "23",
            output_path,
        ]

        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )

        if proc.returncode != 0:
            logger.error("FFmpeg failed: %s", proc.stderr[-500:] if proc.stderr else "no output")
            raise HTTPException(status_code=500, detail="Video encoding failed")

        video_bytes = output_path.read_bytes()

    meta = detection.extra_metadata or {}
    species = meta.get("common_name", "detection")
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in species).strip().replace(" ", "_")
    filename = f"{safe_name}_{str(detection_id)[:8]}.mp4"

    return Response(
        content=video_bytes,
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "public, max-age=3600",
        },
    )
