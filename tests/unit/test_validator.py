"""Unit tests for the eBird validator and Bayesian re-weighting."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from catalog.api.ebird.validator import (
    EBirdValidator,
    Prediction,
    ValidationResult,
    _bayesian_adjust,
)


class TestBayesianAdjust:
    def test_high_confidence_common_bird(self):
        result = _bayesian_adjust(0.95, 0.8)
        assert result > 0.9

    def test_high_confidence_rare_bird(self):
        result = _bayesian_adjust(0.95, 0.01)
        assert result < 0.2

    def test_medium_confidence_peak_season(self):
        result = _bayesian_adjust(0.6, 0.9)
        assert result > 0.6

    def test_medium_confidence_off_season(self):
        result = _bayesian_adjust(0.6, 0.1)
        assert result < 0.3

    def test_zero_frequency_crushes(self):
        result = _bayesian_adjust(0.9, 0.0)
        assert result == 0.0

    def test_perfect_confidence(self):
        result = _bayesian_adjust(1.0, 0.5)
        assert result == 1.0

    def test_zero_confidence(self):
        result = _bayesian_adjust(0.0, 0.5)
        assert result == 0.0

    def test_both_zero(self):
        result = _bayesian_adjust(0.0, 0.0)
        assert result == 0.0

    def test_symmetric_at_50_50(self):
        result = _bayesian_adjust(0.5, 0.5)
        assert abs(result - 0.5) < 0.001


class TestPrediction:
    def test_attributes(self):
        p = Prediction(rank=1, species_code="amerob", common_name="American Robin", confidence=0.92)
        assert p.rank == 1
        assert p.species_code == "amerob"
        assert p.confidence == 0.92


class TestEBirdValidator:
    @pytest.mark.asyncio
    async def test_accepts_valid_top1(self):
        session = AsyncMock()

        local_sp = MagicMock()
        local_sp.species_code = "amerob"
        local_sp.synced_at = None

        exec_results = []
        # local species query
        r1 = MagicMock()
        r1.scalars.return_value.all.return_value = [local_sp]
        exec_results.append(r1)
        # notable codes query
        r2 = MagicMock()
        r2.all.return_value = []
        exec_results.append(r2)
        # frequency query
        r3 = MagicMock()
        r3.first.return_value = (0.75,)
        exec_results.append(r3)

        call_idx = {"i": 0}

        async def mock_exec(stmt):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return exec_results[idx] if idx < len(exec_results) else MagicMock()

        session.execute = mock_exec
        session.add = MagicMock()

        with patch("catalog.api.ebird.validator.write_audit_log", new_callable=AsyncMock) as mock_audit:
            mock_audit.return_value = "fake-uuid"
            validator = EBirdValidator(session=session, region="US-NY-109", confidence_threshold=0.3)
            result = await validator.validate(
                predictions=[
                    Prediction(rank=1, species_code="amerob", common_name="American Robin", confidence=0.92),
                ],
                frame_id="frame-1",
            )

        assert result.ebird_validated is True
        assert result.species_code == "amerob"
        assert result.was_rerouted is False
        assert result.adjusted_confidence > 0.3

    @pytest.mark.asyncio
    async def test_rejects_species_not_on_local_list(self):
        session = AsyncMock()

        # No local species
        r1 = MagicMock()
        r1.scalars.return_value.all.return_value = []
        r2 = MagicMock()
        r2.all.return_value = []
        r3 = MagicMock()
        r3.first.return_value = None

        call_idx = {"i": 0}
        results = [r1, r2, r3]

        async def mock_exec(stmt):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return results[idx] if idx < len(results) else MagicMock()

        session.execute = mock_exec
        session.add = MagicMock()

        with patch("catalog.api.ebird.validator.write_audit_log", new_callable=AsyncMock) as mock_audit:
            mock_audit.return_value = "fake-uuid"
            validator = EBirdValidator(session=session, region="US-NY-109", confidence_threshold=0.3)
            result = await validator.validate(
                predictions=[
                    Prediction(rank=1, species_code="exotic1", common_name="Exotic Bird", confidence=0.95),
                ],
                frame_id="frame-2",
            )

        assert result.ebird_validated is False
        assert result.species_code is None

    @pytest.mark.asyncio
    async def test_reroutes_to_second_candidate(self):
        session = AsyncMock()

        local_sp = MagicMock()
        local_sp.species_code = "houspa"
        local_sp.synced_at = None

        r1 = MagicMock()
        r1.scalars.return_value.all.return_value = [local_sp]
        r2 = MagicMock()
        r2.all.return_value = []

        freq_results = [
            MagicMock(first=MagicMock(return_value=None)),      # exobird: no freq
            MagicMock(first=MagicMock(return_value=(0.6,))),    # houspa: freq
        ]

        call_idx = {"i": 0}
        freq_idx = {"i": 0}

        async def mock_exec(stmt):
            stmt_str = str(stmt) if hasattr(stmt, '__str__') else ""
            idx = call_idx["i"]
            call_idx["i"] += 1
            if idx == 0:
                return r1
            elif idx == 1:
                return r2
            else:
                fi = freq_idx["i"]
                freq_idx["i"] += 1
                return freq_results[fi] if fi < len(freq_results) else MagicMock(first=MagicMock(return_value=None))

        session.execute = mock_exec
        session.add = MagicMock()

        with patch("catalog.api.ebird.validator.write_audit_log", new_callable=AsyncMock) as mock_audit:
            mock_audit.return_value = "fake-uuid"
            validator = EBirdValidator(session=session, region="US-NY-109", confidence_threshold=0.3)
            result = await validator.validate(
                predictions=[
                    Prediction(rank=1, species_code="exobird", common_name="Exotic Bird", confidence=0.85),
                    Prediction(rank=2, species_code="houspa", common_name="House Sparrow", confidence=0.60),
                ],
                frame_id="frame-3",
            )

        assert result.ebird_validated is True
        assert result.species_code == "houspa"
        assert result.was_rerouted is True

    @pytest.mark.asyncio
    async def test_notable_detection_flagged(self):
        session = AsyncMock()

        local_sp = MagicMock()
        local_sp.species_code = "snobun"
        local_sp.synced_at = None

        r1 = MagicMock()
        r1.scalars.return_value.all.return_value = [local_sp]
        r2 = MagicMock()
        r2.all.return_value = [("snobun",)]
        r3 = MagicMock()
        r3.first.return_value = (0.05,)

        call_idx = {"i": 0}
        results = [r1, r2, r3]

        async def mock_exec(stmt):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return results[idx] if idx < len(results) else MagicMock()

        session.execute = mock_exec
        session.add = MagicMock()

        with patch("catalog.api.ebird.validator.write_audit_log", new_callable=AsyncMock) as mock_audit:
            mock_audit.return_value = "fake-uuid"
            validator = EBirdValidator(session=session, region="US-NY-109", confidence_threshold=0.01)
            result = await validator.validate(
                predictions=[
                    Prediction(rank=1, species_code="snobun", common_name="Snow Bunting", confidence=0.70),
                ],
                frame_id="frame-4",
            )

        assert result.is_notable is True
