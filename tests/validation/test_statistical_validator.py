"""
Unit tests for EVO-019 StatisticalValidator (sample size, CV, outliers, normality, constants).
"""

from datetime import UTC, datetime

import numpy as np
import pytest

from product_value_system.validation import BaselineData, StatisticalValidator


@pytest.mark.unit
def test_insufficient_sample_size_blocks_with_exact_suggestion() -> None:
    """Baseline with n < min_sample_size fails and recommends collecting exact deficit."""
    baseline = BaselineData(
        metric_name="latency_ms",
        values=[100.0, 110.0, 105.0, 120.0, 95.0],
        timestamps=[datetime.now(UTC)] * 5,
    )
    result = StatisticalValidator(min_sample_size=30).validate(baseline)

    assert result.valid is False
    assert any("Insufficient samples" in e for e in result.errors)
    assert any("Collect 25 more samples" in s for s in result.suggestions)
    assert result.metrics["sample_size"] == 5.0


@pytest.mark.unit
def test_high_variance_cv_exceeds_threshold() -> None:
    """Baseline with CV > 0.30 triggers high-variance error."""
    rng = np.random.default_rng(42)
    values = list(rng.normal(loc=100.0, scale=50.0, size=100))  # CV ~ 0.50
    baseline = BaselineData(metric_name="latency_ms", values=values)

    result = StatisticalValidator(max_cv=0.30).validate(baseline)

    assert result.valid is False
    assert result.metrics["cv"] > 0.30
    assert any("High variance" in e for e in result.errors)


@pytest.mark.unit
def test_outlier_detection_flags_zscore_extremes() -> None:
    """Z-score outlier detector flags extreme spikes and reports exact count/percentage."""
    values = [100.0] * 95 + [1000.0, 1200.0, 1100.0, 1050.0, 1150.0]
    baseline = BaselineData(metric_name="latency_ms", values=values)

    result = StatisticalValidator(outlier_threshold=3.0).validate(baseline)

    assert result.metrics["outlier_count"] == 5.0
    assert result.metrics["outlier_percentage"] == pytest.approx(5.0)
    assert any("outliers" in w for w in result.warnings)


@pytest.mark.unit
def test_valid_normal_baseline_passes_all_checks() -> None:
    """Well-behaved normal baseline (n=100, CV=0.10) passes with zero errors."""
    rng = np.random.default_rng(42)
    values = list(rng.normal(loc=100.0, scale=10.0, size=100))
    baseline = BaselineData(metric_name="latency_ms", values=values)

    result = StatisticalValidator(min_sample_size=30, max_cv=0.30).validate(baseline)

    assert result.valid is True
    assert result.errors == []
    assert result.metrics["cv"] < 0.20


@pytest.mark.unit
def test_constant_values_flagged_as_error() -> None:
    """Identical baseline observations fail validation as broken metric collection."""
    baseline = BaselineData(metric_name="latency_ms", values=[100.0] * 40)
    result = StatisticalValidator(min_sample_size=30).validate(baseline)

    assert result.valid is False
    assert any("identical" in e.lower() for e in result.errors)


@pytest.mark.unit
def test_empty_baseline_fails_gracefully() -> None:
    """Empty baseline fails cleanly without raising exceptions."""
    baseline = BaselineData(metric_name="latency_ms", values=[])
    result = StatisticalValidator().validate(baseline)

    assert result.valid is False
    assert any("zero observations" in e.lower() for e in result.errors)


@pytest.mark.unit
def test_skewed_distribution_emits_normality_warning() -> None:
    """Strongly skewed exponential baseline warns to consider non-parametric tests."""
    rng = np.random.default_rng(7)
    values = list(100.0 + rng.exponential(scale=12.0, size=80))
    baseline = BaselineData(metric_name="latency_ms", values=values)

    result = StatisticalValidator(min_sample_size=30, max_cv=0.50).validate(baseline)

    assert result.metrics["normality_p_value"] < 0.05
    assert any("not normally distributed" in w for w in result.warnings)


@pytest.mark.unit
def test_binary_success_rate_baseline_validates_cleanly() -> None:
    """Binary 0/1 success-rate baselines pass without false CV/outlier failures."""
    values = [1.0] * 42 + [0.0] * 8
    baseline = BaselineData(metric_name="success_rate", values=values)

    result = StatisticalValidator(min_sample_size=30, max_cv=0.30).validate(baseline)

    assert result.valid is True
    assert result.errors == []
    assert result.metrics["outlier_count"] == 0.0
