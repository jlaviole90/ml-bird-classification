"""Unit tests for the identification audit log module."""

from __future__ import annotations

import time
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from catalog.api.ebird.audit import (
    CandidateEval,
    DecisionTimer,
    DecisionTrace,
    _emit_structured_log,
    write_audit_log,
)


class TestCandidateEval:
    def test_defaults(self):
        c = CandidateEval(rank=1, species_code="amerob", common_name="American Robin", raw_confidence=0.92)
        assert c.on_local_list is None
        assert c.rejection_reason is None
        assert c.adjusted_confidence is None

    def test_serialization(self):
        c = CandidateEval(
            rank=1, species_code="amerob", common_name="American Robin",
            raw_confidence=0.92, on_local_list=True, seasonal_frequency=0.45,
            adjusted_confidence=0.89, rejection_reason=None,
        )
        d = asdict(c)
        assert d["rank"] == 1
        assert d["species_code"] == "amerob"
        assert d["adjusted_confidence"] == 0.89


class TestDecisionTrace:
    def test_build_summary_accepted(self):
        trace = DecisionTrace(
            accepted_rank=1,
            accepted_species="American Robin",
            final_confidence=0.89,
            was_rerouted=False,
            candidates=[
                CandidateEval(rank=1, species_code="amerob", common_name="American Robin", raw_confidence=0.92),
                CandidateEval(rank=2, species_code="eabl", common_name="Eastern Bluebird",
                              raw_confidence=0.04, rejection_reason="adjusted_below_threshold"),
            ],
        )
        summary = trace.build_summary()
        assert "Accepted rank-1 American Robin" in summary
        assert "Eastern Bluebird: adjusted_below_threshold" in summary

    def test_build_summary_rerouted(self):
        trace = DecisionTrace(
            accepted_rank=3,
            accepted_species="House Sparrow",
            final_confidence=0.75,
            was_rerouted=True,
            candidates=[
                CandidateEval(rank=1, species_code="amerob", common_name="American Robin",
                              raw_confidence=0.8, rejection_reason="not_on_local_list"),
            ],
        )
        summary = trace.build_summary()
        assert "Rerouted to rank-3 House Sparrow" in summary

    def test_build_summary_no_acceptance(self):
        trace = DecisionTrace(
            accepted_rank=0,
            accepted_species=None,
            final_confidence=0.0,
            candidates=[
                CandidateEval(rank=1, species_code="amerob", common_name="American Robin",
                              raw_confidence=0.3, rejection_reason="adjusted_below_threshold"),
            ],
        )
        summary = trace.build_summary()
        assert "No candidate accepted" in summary

    def test_build_summary_notable(self):
        trace = DecisionTrace(
            accepted_rank=1,
            accepted_species="Snowy Owl",
            final_confidence=0.85,
            is_notable=True,
        )
        summary = trace.build_summary()
        assert "NOTABLE" in summary


class TestDecisionTimer:
    def test_measures_time(self):
        timer = DecisionTimer()
        with timer:
            time.sleep(0.01)
        assert timer.elapsed_ms > 5  # should be ~10ms, give slack


class TestWriteAuditLog:
    @pytest.mark.asyncio
    async def test_writes_to_session(self):
        session = AsyncMock()
        trace = DecisionTrace(
            frame_id="frame-1",
            model_name="test_model",
            candidates=[
                CandidateEval(rank=1, species_code="amerob", common_name="American Robin", raw_confidence=0.9)
            ],
            ebird_region="US-NY-109",
            ebird_week=15,
        )

        audit_id = await write_audit_log(trace, session)
        assert audit_id is not None
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_uuid(self):
        session = AsyncMock()
        trace = DecisionTrace(frame_id="f1", model_name="m1")
        audit_id = await write_audit_log(trace, session)
        assert len(str(audit_id)) == 36  # UUID format


class TestStructuredLog:
    def test_emit_structured_log(self, caplog):
        trace = DecisionTrace(
            detection_id="det-1",
            frame_id="f-1",
            accepted_species="American Robin",
            accepted_species_code="amerob",
            final_confidence=0.89,
            was_rerouted=False,
            is_notable=False,
            ebird_week=15,
            candidates=[
                CandidateEval(rank=1, species_code="amerob", common_name="American Robin",
                              raw_confidence=0.92),
                CandidateEval(rank=2, species_code="eabl", common_name="Eastern Bluebird",
                              raw_confidence=0.04, rejection_reason="adjusted_below_threshold"),
            ],
        )
        _emit_structured_log(trace)
