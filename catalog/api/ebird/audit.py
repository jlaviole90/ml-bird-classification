"""Identification audit log — structured decision tracing.

Captures the full reasoning chain for every identification: TorchServe's raw
top-5 predictions, eBird validation steps, Bayesian re-weighting, and the final
accept/reject decision with explicit reasons per candidate.

Dual output: PostgreSQL (queryable) and structured JSON logging (stdout).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

try:
    from pythonjsonlogger.json import JsonFormatter as _JsonFormatter
except ImportError:
    from pythonjsonlogger import jsonlogger
    _JsonFormatter = jsonlogger.JsonFormatter
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.api.models.ebird import IdentificationAuditLogORM

PIPELINE_VERSION = "1.0.0"

# ── Structured JSON logger for stdout ───────────────────

_audit_logger = logging.getLogger("audit.identification")
if not _audit_logger.handlers:
    handler = logging.StreamHandler()
    formatter = _JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
    )
    handler.setFormatter(formatter)
    _audit_logger.addHandler(handler)
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False


# ── Data Structures ─────────────────────────────────────


@dataclass
class CandidateEval:
    """Evaluation record for one of the top-N predictions."""

    rank: int
    species_code: str
    common_name: str
    raw_confidence: float
    on_local_list: bool | None = None
    seasonal_frequency: float | None = None
    adjusted_confidence: float | None = None
    rejection_reason: str | None = None


@dataclass
class DecisionTrace:
    """Full audit trail for a single identification decision."""

    detection_id: str = ""
    frame_id: str = ""
    timestamp: str = ""
    pipeline_version: str = PIPELINE_VERSION

    model_name: str = "bird_classifier_v1.0"
    inference_latency_ms: float = 0.0
    candidates: list[CandidateEval] = field(default_factory=list)

    ebird_region: str = ""
    ebird_week: int = 0
    local_list_size: int = 0
    local_list_last_synced: str = ""

    accepted_rank: int = 0
    accepted_species: str | None = None
    accepted_species_code: str | None = None
    final_confidence: float = 0.0
    was_rerouted: bool = False
    is_notable: bool = False
    decision_time_ms: float = 0.0

    summary: str = ""

    def build_summary(self) -> str:
        """Generate a human-readable summary of the decision."""
        parts: list[str] = []

        if self.accepted_rank > 0 and self.accepted_species:
            action = "Rerouted to" if self.was_rerouted else "Accepted"
            parts.append(
                f"{action} rank-{self.accepted_rank} {self.accepted_species} "
                f"({self.final_confidence:.3f})."
            )
        else:
            parts.append("No candidate accepted — detection dropped.")

        for c in self.candidates:
            if c.rejection_reason:
                parts.append(f"Rank-{c.rank} {c.common_name}: {c.rejection_reason}.")

        if self.is_notable:
            parts.append("NOTABLE: species flagged as rare by eBird.")

        self.summary = " ".join(parts)
        return self.summary


class DecisionTimer:
    """Context manager to measure wall time of the validation pass."""

    def __init__(self) -> None:
        self.start_ns: int = 0
        self.elapsed_ms: float = 0.0

    def __enter__(self) -> DecisionTimer:
        self.start_ns = time.perf_counter_ns()
        return self

    def __exit__(self, *_: Any) -> None:
        self.elapsed_ms = (time.perf_counter_ns() - self.start_ns) / 1_000_000


# ── Writers ─────────────────────────────────────────────


async def write_audit_log(trace: DecisionTrace, session: AsyncSession) -> uuid.UUID:
    """Write a DecisionTrace to both PostgreSQL and structured JSON log."""
    audit_id = uuid.uuid4()

    record = IdentificationAuditLogORM(
        id=audit_id,
        detection_id=uuid.UUID(trace.detection_id) if trace.detection_id else None,
        frame_id=trace.frame_id,
        model_name=trace.model_name,
        inference_latency_ms=trace.inference_latency_ms,
        candidates=[asdict(c) for c in trace.candidates],
        ebird_region=trace.ebird_region,
        ebird_week=trace.ebird_week,
        local_list_size=trace.local_list_size,
        local_list_synced_at=(
            datetime.fromisoformat(trace.local_list_last_synced)
            if trace.local_list_last_synced else None
        ),
        accepted_rank=trace.accepted_rank,
        accepted_species_code=trace.accepted_species_code,
        final_confidence=trace.final_confidence,
        was_rerouted=trace.was_rerouted,
        is_notable=trace.is_notable,
        decision_time_ms=trace.decision_time_ms,
        summary=trace.summary,
        pipeline_version=trace.pipeline_version,
    )
    session.add(record)

    _emit_structured_log(trace)

    return audit_id


def _emit_structured_log(trace: DecisionTrace) -> None:
    """Emit a structured JSON log line for real-time monitoring."""
    rejected = [
        f"{c.common_name} ({c.rejection_reason})"
        for c in trace.candidates
        if c.rejection_reason
    ]

    _audit_logger.info(
        "identification_decision",
        extra={
            "detection_id": trace.detection_id,
            "frame_id": trace.frame_id,
            "accepted": trace.accepted_species,
            "accepted_species_code": trace.accepted_species_code,
            "final_confidence": trace.final_confidence,
            "was_rerouted": trace.was_rerouted,
            "is_notable": trace.is_notable,
            "candidates_evaluated": len(trace.candidates),
            "rejected": rejected,
            "decision_time_ms": round(trace.decision_time_ms, 2),
            "ebird_week": trace.ebird_week,
            "pipeline_version": trace.pipeline_version,
        },
    )
