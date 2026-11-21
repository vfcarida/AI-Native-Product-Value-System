"""
Evidence quality scoring based on recency decay, logarithmic sample size,
source epistemic quality (Primary/Secondary/Tertiary), and confidence (EVO-023).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from product_value_system.evidence.models import Evidence, EvidenceQuality


class QualityScorer:
    """Compute normalized 0–1 quality weights for multi-source evidence triangulation."""

    QUALITY_SCORES: dict[EvidenceQuality, float] = {
        EvidenceQuality.PRIMARY: 1.0,
        EvidenceQuality.SECONDARY: 0.6,
        EvidenceQuality.TERTIARY: 0.3,
    }

    def __init__(
        self,
        recency_weight: float = 0.3,
        sample_size_weight: float = 0.4,
        source_quality_weight: float = 0.3,
        *,
        half_life_days: float = 125.0,
    ) -> None:
        total = recency_weight + sample_size_weight + source_quality_weight
        if total <= 0:
            raise ValueError("Quality scorer weights must sum to a positive number.")
        self.recency_weight = recency_weight / total
        self.sample_size_weight = sample_size_weight / total
        self.source_quality_weight = source_quality_weight / total
        self.half_life_days = max(1.0, float(half_life_days))

    def score(self, evidence: Evidence, *, reference_date: datetime | None = None) -> float:
        """Calculate overall quality weight (0–1) for a piece of evidence."""
        breakdown = self.score_breakdown(evidence, reference_date=reference_date)
        return breakdown["overall"]

    def score_breakdown(
        self,
        evidence: Evidence,
        *,
        reference_date: datetime | None = None,
    ) -> dict[str, float]:
        """Return component scores and final weighted quality score (0–1)."""
        recency = self._score_recency(evidence.date, reference_date=reference_date)
        sample = self._score_sample_size(evidence.sample_size)
        source = self._score_source_quality(evidence.quality)

        weighted = (
            recency * self.recency_weight
            + sample * self.sample_size_weight
            + source * self.source_quality_weight
        )
        final_score = max(0.0, min(1.0, weighted * evidence.confidence))
        return {
            "recency": round(recency, 4),
            "sample_size": round(sample, 4),
            "source_quality": round(source, 4),
            "confidence": round(evidence.confidence, 4),
            "overall": round(final_score, 4),
        }

    def _score_recency(
        self,
        date: datetime,
        *,
        reference_date: datetime | None = None,
    ) -> float:
        """Exponential decay with configurable half-life (~125 days -> ~0.37 at 180 days)."""
        if date.tzinfo is not None:
            ref = (
                reference_date.astimezone(UTC)
                if reference_date is not None and reference_date.tzinfo is not None
                else (
                    reference_date.replace(tzinfo=UTC)
                    if reference_date is not None
                    else datetime.now(UTC)
                )
            )
            days_old = (ref - date.astimezone(UTC)).total_seconds() / 86400.0
        else:
            ref = (
                reference_date.replace(tzinfo=None)
                if reference_date is not None
                else datetime.now()
            )
            days_old = (ref - date).total_seconds() / 86400.0

        days_old = max(0.0, days_old)
        decay_rate = math.log(2.0) / self.half_life_days
        return max(0.0, min(1.0, math.exp(-decay_rate * days_old)))

    def _score_sample_size(self, sample_size: int | None) -> float:
        """Logarithmic sample size score: n=1 (~0.10), n=10 (~0.35), n=100 (~0.67), n>=1000 (1)."""
        if sample_size is None:
            return 0.5
        if sample_size <= 0:
            return 0.0
        return max(0.0, min(1.0, math.log10(sample_size + 1) / 3.0))

    def _score_source_quality(self, quality: EvidenceQuality) -> float:
        """Epistemic source hierarchy weight (Primary=1.0, Secondary=0.6, Tertiary=0.3)."""
        return self.QUALITY_SCORES.get(quality, 0.5)
