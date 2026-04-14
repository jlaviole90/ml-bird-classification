"""FastAPI application — bird detection metadata catalog."""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from elasticsearch import Elasticsearch
from fastapi import FastAPI
from prometheus_client import Counter, Histogram, generate_latest
from starlette.responses import Response

from catalog.api.db import async_session, engine
from catalog.api.ebird.client import EBirdClient
from catalog.api.ebird.sync import EBirdSyncService
from catalog.api.models.detection import DetectionORM
import catalog.api.models.ebird as _ebird_models  # noqa: F401 — registers ORM tables
from catalog.api.models.species import Base
from catalog.api.routes import detections, ebird as ebird_routes, search, species

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REQUEST_COUNT = Counter("http_requests_total", "Total HTTP requests", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("http_request_duration_seconds", "Request latency", ["method", "path"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured")

    ebird_client = EBirdClient()
    sync_service = EBirdSyncService(ebird_client, async_session)
    app.state.ebird_client = ebird_client
    app.state.ebird_sync = sync_service

    # Run initial sync before accepting requests
    await _initial_ebird_sync(sync_service)

    scheduler = _start_ebird_scheduler(sync_service)
    app.state.scheduler = scheduler

    _start_catalog_writer()

    yield

    scheduler.shutdown(wait=False)
    await ebird_client.close()
    await engine.dispose()


async def _initial_ebird_sync(sync_service: EBirdSyncService) -> None:
    """Run a one-time sync at startup so the local species list is populated."""
    try:
        n = await sync_service.sync_local_species()
        logger.info("Initial eBird sync: %d local species loaded", n)
        freq = await sync_service.sync_seasonal_frequency()
        logger.info("Initial eBird sync: %d seasonal frequency records loaded", freq)
    except Exception:
        logger.exception("Initial eBird sync failed — validator will use empty list until next scheduled sync")


def _start_ebird_scheduler(sync_service: EBirdSyncService) -> AsyncIOScheduler:
    """Configure APScheduler for periodic eBird data syncs."""
    scheduler = AsyncIOScheduler()

    scheduler.add_job(sync_service.sync_local_species, "interval", hours=24, id="ebird_local_species")
    scheduler.add_job(sync_service.sync_seasonal_frequency, "interval", hours=24, id="ebird_frequency")
    scheduler.add_job(sync_service.sync_notable_sightings, "interval", hours=1, id="ebird_notable")
    scheduler.add_job(sync_service.sync_hotspots, "interval", weeks=1, id="ebird_hotspots")
    scheduler.add_job(sync_service.sync_taxonomy, "interval", days=30, id="ebird_taxonomy")

    scheduler.start()
    logger.info("eBird sync scheduler started")
    return scheduler


app = FastAPI(
    title="Bird Detection Catalog",
    version="2.0.0",
    description="Searchable catalog of bird species detections with eBird-validated identifications",
    lifespan=lifespan,
)

app.include_router(species.router)
app.include_router(detections.router)
app.include_router(search.router)

app.include_router(ebird_routes.router)


@app.middleware("http")
async def metrics_middleware(request, call_next):
    import time

    start = time.time()
    response = await call_next(request)
    elapsed = time.time() - start

    REQUEST_COUNT.labels(request.method, request.url.path, response.status_code).inc()
    REQUEST_LATENCY.labels(request.method, request.url.path).observe(elapsed)
    return response


@app.get("/metrics")
async def prometheus_metrics():
    return Response(content=generate_latest(), media_type="text/plain")


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Kafka catalog writer (background thread) ───────────

def _start_catalog_writer() -> None:
    """Consume from the enriched-metadata Kafka topic and insert into DB + Elasticsearch."""
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "")
    if not bootstrap:
        logger.info("KAFKA_BOOTSTRAP_SERVERS not set — skipping catalog writer")
        return

    topic = os.environ.get("KAFKA_METADATA_TOPIC", "enriched-metadata")
    es_url = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")

    def _run():
        from confluent_kafka import Consumer, KafkaError
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        sync_url = os.environ.get("DATABASE_URL_SYNC")
        if not sync_url:
            _host = os.environ.get("POSTGRES_HOST", "localhost")
            _port = os.environ.get("POSTGRES_PORT", "5432")
            _db = os.environ.get("POSTGRES_DB", "birdcatalog")
            _user = os.environ.get("POSTGRES_USER", "bird")
            _pw = os.environ.get("POSTGRES_PASSWORD", "bird_secret")
            sync_url = f"postgresql://{_user}:{_pw}@{_host}:{_port}/{_db}"
        sync_engine = create_engine(sync_url)
        es = Elasticsearch(es_url)

        consumer = Consumer({
            "bootstrap.servers": bootstrap,
            "group.id": "catalog-writer-group",
            "auto.offset.reset": "latest",
        })
        consumer.subscribe([topic])
        logger.info("Catalog writer subscribed to %s", topic)

        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    logger.error("Kafka error: %s", msg.error())
                continue

            try:
                data = json.loads(msg.value().decode())
                detection = DetectionORM(
                    species_id=data.get("class_id"),
                    confidence=data.get("adjusted_confidence", data["confidence"]),
                    frame_s3_key=data.get("s3_key", ""),
                    source_camera=data.get("source", "birdcam-01"),
                    detected_at=datetime.fromisoformat(data["timestamp"]),
                    extra_metadata={"top5": data.get("top5", []), "frame_id": data.get("frame_id")},
                    raw_confidence=data.get("raw_confidence"),
                    ebird_frequency=data.get("ebird_frequency"),
                    ebird_validated=data.get("ebird_validated", False),
                    validation_notes=data.get("validation_notes", ""),
                )
                with Session(sync_engine) as session:
                    session.add(detection)
                    session.commit()
                    session.refresh(detection)

                    _update_yard_life_list(session, data, detection)

                es_doc = {
                    "detection_id": str(detection.id),
                    "species": data.get("species", ""),
                    "confidence": data.get("adjusted_confidence", data["confidence"]),
                    "source": data.get("source", ""),
                    "detected_at": data["timestamp"],
                    "s3_key": data.get("s3_key", ""),
                    "ebird_validated": data.get("ebird_validated", False),
                    "is_notable": data.get("is_notable", False),
                    "was_rerouted": data.get("was_rerouted", False),
                }
                es.index(index="bird-detections", id=str(detection.id), document=es_doc)

            except Exception:
                logger.exception("Failed to process detection message")

    thread = threading.Thread(target=_run, daemon=True, name="catalog-writer")
    thread.start()


def _update_yard_life_list(session, data: dict, detection: DetectionORM) -> None:
    """Insert or update the yard life list when a validated detection arrives."""
    from catalog.api.models.ebird import YardLifeListORM

    species_code = data.get("species", "")
    if not species_code or not data.get("ebird_validated"):
        return

    existing = session.query(YardLifeListORM).filter_by(species_code=species_code).first()
    confidence = data.get("adjusted_confidence", data.get("confidence", 0))
    ts = datetime.fromisoformat(data["timestamp"])

    if existing:
        existing.last_detected_at = ts
        existing.total_detections += 1
        if confidence > (existing.best_confidence or 0):
            existing.best_confidence = confidence
            existing.best_frame_s3_key = data.get("s3_key", "")
    else:
        entry = YardLifeListORM(
            species_id=data.get("class_id"),
            species_code=species_code,
            first_detected_at=ts,
            last_detected_at=ts,
            total_detections=1,
            best_confidence=confidence,
            best_frame_s3_key=data.get("s3_key", ""),
            ebird_confirmed=True,
        )
        session.add(entry)
    session.commit()
