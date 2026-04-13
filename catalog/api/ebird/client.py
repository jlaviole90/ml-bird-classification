"""Async client for the eBird API 2.0.

Wraps all endpoints used by the pipeline: observations, taxonomy, hotspots,
species lists, and notable sightings. Authenticated via X-eBirdApiToken header.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

import httpx

logger = logging.getLogger(__name__)

EBIRD_BASE_URL = "https://api.ebird.org/v2"


class EBirdClient:
    """Thin async wrapper around the eBird API 2.0."""

    def __init__(
        self,
        api_key: str | None = None,
        region: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
        radius_km: int = 25,
        timeout: float = 30.0,
    ):
        self.api_key = api_key or os.environ.get("EBIRD_API_KEY", "")
        self.region = region or os.environ.get("EBIRD_REGION", "")
        self.lat = lat or float(os.environ.get("EBIRD_LAT", "0"))
        self.lng = lng or float(os.environ.get("EBIRD_LNG", "0"))
        self.radius_km = radius_km
        self._client = httpx.AsyncClient(
            base_url=EBIRD_BASE_URL,
            headers={"X-eBirdApiToken": self.api_key},
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    # ── Observations ────────────────────────────────────────

    async def get_recent_observations(
        self,
        region: str | None = None,
        back: int = 14,
        max_results: int = 10000,
    ) -> list[dict]:
        """Recent observations in a region (up to `back` days)."""
        return await self._get(
            f"/data/obs/{region or self.region}/recent",
            params={"back": back, "maxResults": max_results},
        )

    async def get_recent_species_observations(
        self,
        species_code: str,
        region: str | None = None,
        back: int = 30,
    ) -> list[dict]:
        """Recent observations of a specific species in a region."""
        return await self._get(
            f"/data/obs/{region or self.region}/recent/{species_code}",
            params={"back": back},
        )

    async def get_nearby_observations(
        self,
        lat: float | None = None,
        lng: float | None = None,
        dist: int | None = None,
        back: int = 14,
    ) -> list[dict]:
        """Recent observations near coordinates."""
        return await self._get(
            "/data/obs/geo/recent",
            params={
                "lat": lat or self.lat,
                "lng": lng or self.lng,
                "dist": dist or self.radius_km,
                "back": back,
            },
        )

    async def get_notable_observations(
        self,
        region: str | None = None,
        back: int = 14,
    ) -> list[dict]:
        """Recent notable (rare) sightings in a region."""
        return await self._get(
            f"/data/obs/{region or self.region}/recent/notable",
            params={"back": back},
        )

    async def get_historic_observations(
        self,
        region: str | None = None,
        year: int | None = None,
        month: int | None = None,
        day: int | None = None,
    ) -> list[dict]:
        """Observations on a specific date in a region."""
        d = date.today()
        y, m, dy = year or d.year, month or d.month, day or d.day
        return await self._get(
            f"/data/obs/{region or self.region}/historic/{y}/{m}/{dy}",
        )

    # ── Species Lists ───────────────────────────────────────

    async def get_species_list(self, region: str | None = None) -> list[str]:
        """Full species code list ever recorded in a region."""
        return await self._get(f"/product/spplist/{region or self.region}")

    async def get_region_stats(
        self,
        region: str | None = None,
        year: int | None = None,
        month: int | None = None,
        day: int | None = None,
    ) -> dict:
        """Daily checklist stats for a region."""
        d = date.today()
        y, m, dy = year or d.year, month or d.month, day or d.day
        return await self._get(
            f"/product/stats/{region or self.region}/{y}/{m}/{dy}",
        )

    # ── Taxonomy ────────────────────────────────────────────

    async def get_taxonomy(
        self,
        species_codes: list[str] | None = None,
        locale: str = "en",
    ) -> list[dict]:
        """Full eBird taxonomy or a subset filtered by species codes."""
        params: dict[str, Any] = {"fmt": "json", "locale": locale}
        if species_codes:
            params["species"] = ",".join(species_codes)
        return await self._get("/ref/taxonomy/ebird", params=params)

    # ── Hotspots ────────────────────────────────────────────

    async def get_nearby_hotspots(
        self,
        lat: float | None = None,
        lng: float | None = None,
        dist: int | None = None,
    ) -> list[dict]:
        """Birding hotspots near coordinates."""
        return await self._get(
            "/ref/hotspot/geo",
            params={
                "lat": lat or self.lat,
                "lng": lng or self.lng,
                "dist": dist or self.radius_km,
                "fmt": "json",
            },
        )

    async def get_hotspot_info(self, loc_id: str) -> dict:
        """Detailed info for a single hotspot."""
        return await self._get(f"/ref/hotspot/info/{loc_id}")

    async def get_region_hotspots(self, region: str | None = None) -> list[dict]:
        """All hotspots in a region."""
        return await self._get(
            f"/ref/hotspot/{region or self.region}",
            params={"fmt": "json"},
        )
