"""FastAPI application — bird detection metadata catalog."""

from __future__ import annotations

import logging

from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from prometheus_client import Counter, Histogram, generate_latest
from starlette.responses import Response

from catalog.api.db import async_session, engine
from catalog.api.ebird.client import EBirdClient
from catalog.api.ebird.sync import EBirdSyncService
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

    await _initial_ebird_sync(sync_service)

    scheduler = _start_ebird_scheduler(sync_service)
    app.state.scheduler = scheduler

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
    version="3.0.0",
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
