"""Unit tests for eBird Pydantic models/schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from catalog.api.models.ebird import (
    AuditLogResponse,
    AuditStatsResponse,
    CandidateEvalSchema,
    HotspotResponse,
    LocalSpeciesResponse,
    NotableSightingResponse,
    YardLifeListEntry,
    YardListStats,
)


class TestLocalSpeciesResponse:
    def test_minimal(self):
        sp = LocalSpeciesResponse(species_code="amerob", common_name="American Robin")
        assert sp.species_code == "amerob"
        assert sp.current_week_frequency is None

    def test_full(self):
        sp = LocalSpeciesResponse(
            species_code="amerob",
            common_name="American Robin",
            scientific_name="Turdus migratorius",
            last_observed=date(2026, 4, 10),
            observation_count=42,
            is_notable=False,
            current_week_frequency=0.85,
        )
        assert sp.observation_count == 42
        assert sp.current_week_frequency == 0.85


class TestNotableSightingResponse:
    def test_create(self):
        ns = NotableSightingResponse(
            id=1,
            species_code="snobun",
            common_name="Snow Bunting",
            observed_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
            lat=42.0,
            lng=-76.0,
            location_name="Test Park",
        )
        assert ns.common_name == "Snow Bunting"


class TestHotspotResponse:
    def test_create(self):
        hs = HotspotResponse(
            hotspot_id="L12345",
            name="Central Park",
            lat=40.78,
            lng=-73.97,
            num_species=200,
        )
        assert hs.num_species == 200


class TestYardLifeListEntry:
    def test_create(self):
        entry = YardLifeListEntry(
            id=1,
            species_code="amerob",
            first_detected_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            last_detected_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
            total_detections=15,
            best_confidence=0.95,
            ebird_confirmed=True,
        )
        assert entry.total_detections == 15
        assert entry.ebird_confirmed is True


class TestYardListStats:
    def test_create(self):
        stats = YardListStats(
            total_species=34,
            total_detections=1200,
            ebird_confirmed_count=30,
            local_list_size=187,
            coverage_pct=18.2,
        )
        assert stats.coverage_pct == 18.2
        assert stats.latest_new_species is None


class TestCandidateEvalSchema:
    def test_accepted(self):
        c = CandidateEvalSchema(
            rank=1, species_code="amerob", common_name="American Robin",
            raw_confidence=0.92, on_local_list=True, seasonal_frequency=0.8,
            adjusted_confidence=0.89,
        )
        assert c.rejection_reason is None

    def test_rejected(self):
        c = CandidateEvalSchema(
            rank=2, species_code="eabl", common_name="Eastern Bluebird",
            raw_confidence=0.04, rejection_reason="adjusted_below_threshold",
        )
        assert c.rejection_reason == "adjusted_below_threshold"


class TestAuditLogResponse:
    def test_create(self):
        audit = AuditLogResponse(
            id=uuid.uuid4(),
            frame_id="f-1",
            created_at=datetime.now(timezone.utc),
            model_name="bird_classifier_v1.0",
            candidates=[
                CandidateEvalSchema(rank=1, species_code="amerob", common_name="American Robin", raw_confidence=0.92)
            ],
            accepted_rank=1,
            accepted_species_code="amerob",
            final_confidence=0.89,
        )
        assert len(audit.candidates) == 1


class TestAuditStatsResponse:
    def test_create(self):
        stats = AuditStatsResponse(
            total_decisions=1000,
            rerouted_count=50,
            reroute_rate=0.05,
            rejected_count=10,
            rejection_rate=0.01,
            top_rejection_reasons=[],
            top_reroute_pairs=[],
        )
        assert stats.reroute_rate == 0.05
