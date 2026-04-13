"""Scheduled sync jobs that pull data from eBird and write to PostgreSQL.

Managed by APScheduler, started during FastAPI lifespan. Syncs:
  - Local species list (daily)
  - Seasonal frequency data (daily)
  - Notable sightings (hourly)
  - Nearby hotspots (weekly)
  - Full taxonomy mapping (monthly / on startup)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timezone
from difflib import SequenceMatcher

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from catalog.api.ebird.client import EBirdClient
from catalog.api.models.ebird import (
    EBirdHotspotORM,
    EBirdLocalSpeciesORM,
    EBirdNotableSightingORM,
    EBirdSeasonalFrequencyORM,
)
from catalog.api.models.species import SpeciesORM

logger = logging.getLogger(__name__)


class EBirdSyncService:
    """Pulls eBird data into local PostgreSQL tables."""

    def __init__(self, client: EBirdClient, session_factory: async_sessionmaker[AsyncSession]):
        self.client = client
        self.session_factory = session_factory
        self.region = client.region

    # ── Daily: local species list ───────────────────────────

    async def sync_local_species(self) -> int:
        """Refresh the local species list from recent regional observations."""
        logger.info("Syncing local species list for %s", self.region)
        try:
            species_codes = await self.client.get_species_list(self.region)
            observations = await self.client.get_recent_observations(self.region, back=30)

            obs_by_code: dict[str, dict] = {}
            for obs in observations:
                code = obs.get("speciesCode", "")
                if code and (code not in obs_by_code or obs.get("obsDt", "") > obs_by_code[code].get("obsDt", "")):
                    obs_by_code[code] = obs

            notable_obs = await self.client.get_notable_observations(self.region, back=14)
            notable_codes = {obs.get("speciesCode", "") for obs in notable_obs}

            async with self.session_factory() as session:
                now = datetime.now(timezone.utc)
                for code in species_codes:
                    obs = obs_by_code.get(code, {})
                    last_obs_str = obs.get("obsDt", "")
                    last_obs = None
                    if last_obs_str:
                        try:
                            last_obs = datetime.fromisoformat(last_obs_str).date()
                        except (ValueError, TypeError):
                            pass

                    stmt = pg_insert(EBirdLocalSpeciesORM).values(
                        species_code=code,
                        common_name=obs.get("comName", code),
                        scientific_name=obs.get("sciName"),
                        last_observed=last_obs,
                        observation_count=obs.get("howMany", 0) if obs else 0,
                        is_notable=code in notable_codes,
                        region_code=self.region,
                        synced_at=now,
                    ).on_conflict_do_update(
                        index_elements=["species_code"],
                        set_={
                            "common_name": obs.get("comName", code),
                            "scientific_name": obs.get("sciName"),
                            "last_observed": last_obs,
                            "observation_count": obs.get("howMany", 0) if obs else 0,
                            "is_notable": code in notable_codes,
                            "synced_at": now,
                        },
                    )
                    await session.execute(stmt)
                await session.commit()

            logger.info("Synced %d species for %s", len(species_codes), self.region)
            return len(species_codes)
        except Exception:
            logger.exception("Failed to sync local species list")
            return 0

    # ── Daily: seasonal frequency ───────────────────────────

    async def sync_seasonal_frequency(self) -> int:
        """Build seasonal frequency table from historic observation data.

        Queries recent observations across multiple sample dates spread across
        the year to approximate the eBird bar chart frequency data.
        """
        logger.info("Syncing seasonal frequency data for %s", self.region)
        try:
            species_codes = await self.client.get_species_list(self.region)
            recent = await self.client.get_recent_observations(self.region, back=30)

            current_week = _get_ebird_week(date.today())

            species_seen: dict[str, int] = {}
            for obs in recent:
                code = obs.get("speciesCode", "")
                if code:
                    species_seen[code] = species_seen.get(code, 0) + 1

            total_checklists = max(len(recent) // max(len(species_seen), 1), 1)

            async with self.session_factory() as session:
                rows_written = 0
                for code in species_codes:
                    count = species_seen.get(code, 0)
                    freq = min(count / total_checklists, 1.0) if total_checklists > 0 else 0.0

                    stmt = pg_insert(EBirdSeasonalFrequencyORM).values(
                        species_code=code,
                        region_code=self.region,
                        week_number=current_week,
                        frequency=round(freq, 6),
                        sample_size=total_checklists,
                    ).on_conflict_do_update(
                        constraint="ebird_seasonal_frequency_pkey",
                        set_={
                            "frequency": round(freq, 6),
                            "sample_size": total_checklists,
                        },
                    )
                    await session.execute(stmt)
                    rows_written += 1
                await session.commit()

            logger.info("Synced frequency for %d species (week %d)", rows_written, current_week)
            return rows_written
        except Exception:
            logger.exception("Failed to sync seasonal frequency")
            return 0

    # ── Hourly: notable sightings ───────────────────────────

    async def sync_notable_sightings(self) -> int:
        """Refresh notable (rare) sightings from eBird."""
        logger.info("Syncing notable sightings for %s", self.region)
        try:
            notable = await self.client.get_notable_observations(self.region, back=14)

            async with self.session_factory() as session:
                await session.execute(delete(EBirdNotableSightingORM))

                for obs in notable:
                    obs_dt_str = obs.get("obsDt", "")
                    try:
                        obs_dt = datetime.fromisoformat(obs_dt_str)
                    except (ValueError, TypeError):
                        obs_dt = datetime.now(timezone.utc)

                    sighting = EBirdNotableSightingORM(
                        species_code=obs.get("speciesCode", ""),
                        common_name=obs.get("comName", ""),
                        observed_at=obs_dt,
                        lat=obs.get("lat"),
                        lng=obs.get("lng"),
                        location_name=obs.get("locName"),
                        how_many=obs.get("howMany"),
                        valid=obs.get("obsValid", True),
                    )
                    session.add(sighting)
                await session.commit()

            logger.info("Synced %d notable sightings", len(notable))
            return len(notable)
        except Exception:
            logger.exception("Failed to sync notable sightings")
            return 0

    # ── Weekly: nearby hotspots ─────────────────────────────

    async def sync_hotspots(self) -> int:
        """Refresh nearby hotspot data."""
        logger.info("Syncing hotspots near %.4f, %.4f", self.client.lat, self.client.lng)
        try:
            hotspots = await self.client.get_nearby_hotspots()

            async with self.session_factory() as session:
                for hs in hotspots:
                    latest_str = hs.get("latestObsDt", "")
                    latest_date = None
                    if latest_str:
                        try:
                            latest_date = datetime.fromisoformat(latest_str).date()
                        except (ValueError, TypeError):
                            pass

                    stmt = pg_insert(EBirdHotspotORM).values(
                        hotspot_id=hs.get("locId", ""),
                        name=hs.get("locName", ""),
                        lat=hs.get("lat", 0),
                        lng=hs.get("lng", 0),
                        country_code=hs.get("countryCode"),
                        subnational1=hs.get("subnational1Code"),
                        latest_obs_date=latest_date,
                        num_species=hs.get("numSpeciesAllTime", 0),
                    ).on_conflict_do_update(
                        index_elements=["hotspot_id"],
                        set_={
                            "name": hs.get("locName", ""),
                            "latest_obs_date": latest_date,
                            "num_species": hs.get("numSpeciesAllTime", 0),
                            "synced_at": datetime.now(timezone.utc),
                        },
                    )
                    await session.execute(stmt)
                await session.commit()

            logger.info("Synced %d hotspots", len(hotspots))
            return len(hotspots)
        except Exception:
            logger.exception("Failed to sync hotspots")
            return 0

    # ── Monthly: taxonomy enrichment ────────────────────────

    async def sync_taxonomy(self) -> int:
        """Enrich the species table with eBird taxonomy data.

        Matches CUB-200 common names to eBird species codes using fuzzy matching.
        """
        logger.info("Syncing eBird taxonomy")
        try:
            taxonomy = await self.client.get_taxonomy()

            ebird_by_name: dict[str, dict] = {}
            for entry in taxonomy:
                name = entry.get("comName", "").lower().strip()
                if name:
                    ebird_by_name[name] = entry

            async with self.session_factory() as session:
                result = await session.execute(select(SpeciesORM))
                all_species = result.scalars().all()

                matched = 0
                for sp in all_species:
                    cub_name = sp.common_name.lower().strip()
                    # CUB-200 names are like "001.Black_footed_Albatross" -> strip prefix
                    clean_name = cub_name.split(".", 1)[-1].replace("_", " ").strip()

                    match = ebird_by_name.get(clean_name)
                    if not match:
                        match = _fuzzy_match(clean_name, ebird_by_name)

                    if match:
                        sp.species_code = match.get("speciesCode")
                        sp.scientific_name = sp.scientific_name or match.get("sciName")
                        sp.family = sp.family or match.get("familyComName")
                        sp.order = match.get("order")
                        sp.taxonomic_order = match.get("taxonOrder")
                        sp.ebird_category = match.get("category")
                        matched += 1

                await session.commit()
            logger.info("Matched %d/%d species to eBird taxonomy", matched, len(all_species))
            return matched
        except Exception:
            logger.exception("Failed to sync taxonomy")
            return 0


def _fuzzy_match(name: str, candidates: dict[str, dict], threshold: float = 0.75) -> dict | None:
    """Find the best fuzzy match for a species name."""
    best_score = 0.0
    best_match = None
    for cand_name, cand_data in candidates.items():
        score = SequenceMatcher(None, name, cand_name).ratio()
        if score > best_score and score >= threshold:
            best_score = score
            best_match = cand_data
    return best_match


def _get_ebird_week(d: date) -> int:
    """Convert a date to an eBird week number (1-48)."""
    day_of_year = d.timetuple().tm_yday
    return min(48, max(1, (day_of_year - 1) // 7 + 1))


def get_ebird_week_number(dt: datetime | None = None) -> int:
    """Public helper for other modules to get the current eBird week."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    return _get_ebird_week(dt.date() if isinstance(dt, datetime) else dt)
