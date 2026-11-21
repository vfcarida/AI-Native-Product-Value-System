"""
Unit tests for Evidence QualityScorer (recency decay, sample size, source hierarchy) (EVO-023).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from product_value_system.evidence import (
    Evidence,
    EvidenceQuality,
    EvidenceType,
    QualityScorer,
)


def _ev(
    *,
    days_old: int = 0,
    sample_size: int | None = 100,
    quality: EvidenceQuality = EvidenceQuality.PRIMARY,
    ev_type: EvidenceType = EvidenceType.INTERVIEW,
    confidence: float = 1.0,
    tz_aware: bool = True,
) -> Evidence:
    now = datetime.now(UTC) if tz_aware else datetime.now()
    return Evidence(
        id="ev-test",
        type=ev_type,
        quality=quality,
        source="test-source",
        date=now - timedelta(days=days_old),
        claim="Test claim",
        supporting_data={},
        sample_size=sample_size,
        confidence=confidence,
    )


@pytest.mark.unit
def test_recency_exponential_decay() -> None:
    """Recent evidence scores near 1.0 while 1-year-old evidence decays sharply."""
    scorer = QualityScorer()
    recent_recency = scorer._score_recency(datetime.now(UTC) - timedelta(days=7))
    mid_recency = scorer._score_recency(datetime.now(UTC) - timedelta(days=180))
    old_recency = scorer._score_recency(datetime.now(UTC) - timedelta(days=365))

    assert recent_recency > 0.9
    assert 0.30 <= mid_recency <= 0.45
    assert old_recency < 0.2
    assert recent_recency > mid_recency > old_recency


@pytest.mark.unit
def test_recency_handles_naive_and_aware_datetimes() -> None:
    """QualityScorer safely scores both timezone-aware and naive datetimes."""
    scorer = QualityScorer()
    aware_score = scorer.score(_ev(days_old=5, tz_aware=True))
    naive_score = scorer.score(_ev(days_old=5, tz_aware=False))
    assert aware_score == pytest.approx(naive_score, abs=0.01)


@pytest.mark.unit
def test_sample_size_logarithmic_scaling() -> None:
    """Larger sample sizes monotonically increase quality score up to 1000+ saturation."""
    scorer = QualityScorer()
    s0 = scorer._score_sample_size(0)
    s1 = scorer._score_sample_size(1)
    s10 = scorer._score_sample_size(10)
    s100 = scorer._score_sample_size(100)
    s1000 = scorer._score_sample_size(1000)
    s_none = scorer._score_sample_size(None)

    assert s0 == 0.0
    assert s1 < s10 < s100 < s1000
    assert s1000 == pytest.approx(1.0)
    assert s_none == pytest.approx(0.5)


@pytest.mark.unit
def test_large_analytics_outweighs_single_interview() -> None:
    """Recent n=1000 primary analytics outscores single-participant interview."""
    scorer = QualityScorer()
    large = _ev(days_old=0, sample_size=1000, ev_type=EvidenceType.ANALYTICS)
    small = _ev(days_old=0, sample_size=1, ev_type=EvidenceType.INTERVIEW)

    large_score = scorer.score(large)
    small_score = scorer.score(small)
    assert large_score > 0.9
    assert small_score < 0.65
    assert large_score > small_score


@pytest.mark.unit
def test_primary_secondary_tertiary_hierarchy() -> None:
    """Primary sources outscore secondary, which outscore tertiary (holding other factors equal)."""
    scorer = QualityScorer()
    primary = scorer.score(_ev(quality=EvidenceQuality.PRIMARY, sample_size=50))
    secondary = scorer.score(_ev(quality=EvidenceQuality.SECONDARY, sample_size=50))
    tertiary = scorer.score(_ev(quality=EvidenceQuality.TERTIARY, sample_size=50))

    assert primary > secondary > tertiary


@pytest.mark.unit
def test_high_vs_low_overall_quality_verification() -> None:
    """Matches EVO-023 verification: recent n=5000 primary >> 400-day-old n=1 secondary."""
    scorer = QualityScorer()
    high_q = _ev(
        days_old=0,
        sample_size=5000,
        quality=EvidenceQuality.PRIMARY,
        ev_type=EvidenceType.ANALYTICS,
    )
    low_q = _ev(
        days_old=400,
        sample_size=1,
        quality=EvidenceQuality.SECONDARY,
        ev_type=EvidenceType.COMPETITOR_RESEARCH,
    )
    assert scorer.score(high_q) > 0.95
    assert scorer.score(low_q) < 0.30


@pytest.mark.unit
def test_confidence_multiplier_and_breakdown() -> None:
    """Evidence confidence scales the overall weighted score and appears in score_breakdown."""
    scorer = QualityScorer()
    full_conf = _ev(confidence=1.0, sample_size=1000)
    half_conf = _ev(confidence=0.5, sample_size=1000)

    assert scorer.score(half_conf) == pytest.approx(scorer.score(full_conf) * 0.5, abs=0.001)
    breakdown = scorer.score_breakdown(half_conf)
    assert set(breakdown.keys()) == {
        "recency",
        "sample_size",
        "source_quality",
        "confidence",
        "overall",
    }
    assert breakdown["confidence"] == pytest.approx(0.5)


@pytest.mark.unit
def test_custom_weights_auto_normalize_and_reject_non_positive() -> None:
    """Custom weights auto-normalize; non-positive total weight raises ValueError."""
    scorer = QualityScorer(recency_weight=2.0, sample_size_weight=1.0, source_quality_weight=1.0)
    assert scorer.recency_weight == pytest.approx(0.5)
    assert scorer.sample_size_weight == pytest.approx(0.25)

    with pytest.raises(ValueError, match="positive"):
        QualityScorer(recency_weight=0.0, sample_size_weight=0.0, source_quality_weight=0.0)
