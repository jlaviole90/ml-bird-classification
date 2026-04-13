"""eBird-backed species validation and Bayesian confidence re-weighting.

Pipeline: raw TorchServe top-5 → local list check → seasonal frequency lookup →
Bayesian adjustment → re-rank candidates → notable check → emit DecisionTrace.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.api.ebird.audit import (
    CandidateEval,
    DecisionTimer,
    DecisionTrace,
    write_audit_log,
)
from catalog.api.ebird.sync import get_ebird_week_number
from catalog.api.models.ebird import (
    EBirdLocalSpeciesORM,
    EBirdNotableSightingORM,
    EBirdSeasonalFrequencyORM,
)

logger = logging.getLogger(__name__)

DEFAULT_CONFIDENCE_THRESHOLD = 0.3


class Prediction:
    """A single model prediction from TorchServe top-5."""

    def __init__(self, rank: int, species_code: str, common_name: str, confidence: float):
        self.rank = rank
        self.species_code = species_code
        self.common_name = common_name
        self.confidence = confidence


class ValidationResult:
    """Result returned to the pipeline after validation."""

    def __init__(
        self,
        species_code: str | None,
        common_name: str | None,
        raw_confidence: float,
        adjusted_confidence: float,
        ebird_frequency: float | None,
        ebird_validated: bool,
        was_rerouted: bool,
        is_notable: bool,
        validation_notes: str,
        audit_id: uuid.UUID | None,
    ):
        self.species_code = species_code
        self.common_name = common_name
        self.raw_confidence = raw_confidence
        self.adjusted_confidence = adjusted_confidence
        self.ebird_frequency = ebird_frequency
        self.ebird_validated = ebird_validated
        self.was_rerouted = was_rerouted
        self.is_notable = is_notable
        self.validation_notes = validation_notes
        self.audit_id = audit_id


class EBirdValidator:
    """Validates TorchServe predictions against eBird data."""

    def __init__(
        self,
        session: AsyncSession,
        region: str | None = None,
        confidence_threshold: float | None = None,
        model_name: str = "bird_classifier_v1.0",
    ):
        self.session = session
        self.region = region or os.environ.get("EBIRD_REGION", "")
        self.confidence_threshold = confidence_threshold or float(
            os.environ.get("CONFIDENCE_THRESHOLD", DEFAULT_CONFIDENCE_THRESHOLD)
        )
        self.model_name = model_name

    async def validate(
        self,
        predictions: list[Prediction],
        frame_id: str = "",
        detection_id: str = "",
        inference_latency_ms: float = 0.0,
    ) -> ValidationResult:
        """Run the full validation pipeline on a set of TorchServe predictions.

        Returns a ValidationResult with the accepted species (or None if all
        candidates were rejected) plus an audit_id linking to the full trace.
        """
        timer = DecisionTimer()
        with timer:
            now = datetime.now(timezone.utc)
            week = get_ebird_week_number(now)

            local_species = await self._load_local_species()
            local_codes = {s.species_code for s in local_species}
            local_list_size = len(local_codes)

            synced_at = ""
            if local_species:
                synced_at = (local_species[0].synced_at or now).isoformat()

            notable_codes = await self._load_notable_codes()

            candidates: list[CandidateEval] = []
            for pred in predictions:
                cand = CandidateEval(
                    rank=pred.rank,
                    species_code=pred.species_code,
                    common_name=pred.common_name,
                    raw_confidence=pred.confidence,
                )

                # Step 1: local list check
                cand.on_local_list = pred.species_code in local_codes

                # Step 2: seasonal frequency lookup
                freq = await self._get_frequency(pred.species_code, week)
                cand.seasonal_frequency = freq

                # Step 3: Bayesian re-weighting
                if cand.on_local_list and freq is not None and freq > 0:
                    cand.adjusted_confidence = _bayesian_adjust(pred.confidence, freq)
                elif cand.on_local_list and freq is not None and freq == 0:
                    cand.adjusted_confidence = pred.confidence * 0.1
                elif not cand.on_local_list:
                    cand.adjusted_confidence = 0.0
                else:
                    # No frequency data available — use raw confidence with slight penalty
                    cand.adjusted_confidence = pred.confidence * 0.8

                candidates.append(cand)

            # Step 4: Re-rank by adjusted confidence, accept best above threshold
            ranked = sorted(candidates, key=lambda c: c.adjusted_confidence or 0, reverse=True)
            accepted: CandidateEval | None = None

            for cand in ranked:
                if cand.adjusted_confidence is None or cand.adjusted_confidence < self.confidence_threshold:
                    if not cand.on_local_list:
                        cand.rejection_reason = "not_on_local_list"
                    elif cand.seasonal_frequency is not None and cand.seasonal_frequency == 0:
                        cand.rejection_reason = "seasonal_frequency_zero"
                    else:
                        cand.rejection_reason = "adjusted_below_threshold"
                elif accepted is not None:
                    cand.rejection_reason = f"outranked_by_candidate_{accepted.rank}"
                else:
                    accepted = cand

            # Tag remaining unrejected candidates that weren't accepted
            for cand in ranked:
                if cand is not accepted and cand.rejection_reason is None:
                    if accepted:
                        cand.rejection_reason = f"outranked_by_candidate_{accepted.rank}"
                    else:
                        cand.rejection_reason = "adjusted_below_threshold"

            # Step 5: Notable check
            is_notable = False
            if accepted and accepted.species_code in notable_codes:
                is_notable = True

            was_rerouted = accepted is not None and accepted.rank != 1

        # Build trace
        trace = DecisionTrace(
            detection_id=detection_id,
            frame_id=frame_id,
            timestamp=now.isoformat(),
            model_name=self.model_name,
            inference_latency_ms=inference_latency_ms,
            candidates=candidates,
            ebird_region=self.region,
            ebird_week=week,
            local_list_size=local_list_size,
            local_list_last_synced=synced_at,
            accepted_rank=accepted.rank if accepted else 0,
            accepted_species=accepted.common_name if accepted else None,
            accepted_species_code=accepted.species_code if accepted else None,
            final_confidence=accepted.adjusted_confidence if accepted else 0.0,
            was_rerouted=was_rerouted,
            is_notable=is_notable,
            decision_time_ms=timer.elapsed_ms,
        )
        trace.build_summary()

        audit_id = await write_audit_log(trace, self.session)

        return ValidationResult(
            species_code=accepted.species_code if accepted else None,
            common_name=accepted.common_name if accepted else None,
            raw_confidence=predictions[0].confidence if predictions else 0.0,
            adjusted_confidence=accepted.adjusted_confidence if accepted else 0.0,
            ebird_frequency=accepted.seasonal_frequency if accepted else None,
            ebird_validated=accepted is not None,
            was_rerouted=was_rerouted,
            is_notable=is_notable,
            validation_notes=trace.summary,
            audit_id=audit_id,
        )

    # ── Internal queries ────────────────────────────────────

    async def _load_local_species(self) -> list[EBirdLocalSpeciesORM]:
        result = await self.session.execute(
            select(EBirdLocalSpeciesORM).where(
                EBirdLocalSpeciesORM.region_code == self.region
            )
        )
        return list(result.scalars().all())

    async def _load_notable_codes(self) -> set[str]:
        result = await self.session.execute(
            select(EBirdNotableSightingORM.species_code).distinct()
        )
        return {row[0] for row in result.all()}

    async def _get_frequency(self, species_code: str, week: int) -> float | None:
        result = await self.session.execute(
            select(EBirdSeasonalFrequencyORM.frequency).where(
                EBirdSeasonalFrequencyORM.species_code == species_code,
                EBirdSeasonalFrequencyORM.region_code == self.region,
                EBirdSeasonalFrequencyORM.week_number == week,
            )
        )
        row = result.first()
        return row[0] if row else None


def _bayesian_adjust(raw_confidence: float, frequency: float) -> float:
    """Bayesian-style adjustment: P(species|detection) using seasonal frequency as prior.

    adjusted = (raw * freq) / (raw * freq + (1 - raw) * (1 - freq))
    """
    numerator = raw_confidence * frequency
    denominator = numerator + (1 - raw_confidence) * (1 - frequency)
    if denominator == 0:
        return 0.0
    return numerator / denominator
