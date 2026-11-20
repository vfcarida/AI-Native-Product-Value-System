"""
Unit tests for EVO-019 PowerAnalyzer (MDE, achieved power, sample size, metric types).
"""

import numpy as np
import pytest

from product_value_system.validation import BaselineData, PowerAnalyzer


@pytest.mark.unit
def test_power_analysis_sufficient_sample_size() -> None:
    """Large low-variance baseline achieves >=0.80 power and sufficient=True."""
    rng = np.random.default_rng(42)
    values = list(rng.normal(loc=100.0, scale=15.0, size=200))
    baseline = BaselineData(metric_name="latency_ms", values=values)

    analysis = PowerAnalyzer(target_power=0.80).analyze(baseline, effect_size_percent=10.0)

    assert analysis.sufficient is True
    assert analysis.current_power >= 0.80
    assert analysis.recommended_sample_size <= 200
    assert analysis.minimum_detectable_effect_percent <= 10.0


@pytest.mark.unit
def test_power_analysis_insufficient_with_small_sample_and_zero_variance() -> None:
    """Small constant-value baseline handles zero variance safely and flags underpowered."""
    baseline = BaselineData(metric_name="latency_ms", values=[100.0] * 20)

    analysis = PowerAnalyzer(target_power=0.80).analyze(baseline, effect_size_percent=5.0)

    assert analysis.sufficient is False
    assert analysis.current_power < 0.80
    assert analysis.recommended_sample_size > 20


@pytest.mark.unit
def test_power_analysis_supports_absolute_effect_size() -> None:
    """Providing absolute effect_size (e.g., 15ms reduction) works equivalently to percent."""
    rng = np.random.default_rng(10)
    values = list(rng.normal(loc=150.0, scale=20.0, size=250))
    baseline = BaselineData(metric_name="latency_ms", values=values)

    by_abs = PowerAnalyzer().analyze(baseline, effect_size=15.0)
    by_pct = PowerAnalyzer().analyze(baseline, effect_size_percent=10.0)

    assert by_abs.current_power == pytest.approx(by_pct.current_power, rel=0.05)
    assert by_abs.recommended_sample_size == pytest.approx(by_pct.recommended_sample_size, rel=0.10)


@pytest.mark.unit
def test_power_analysis_binary_conversion_rate_metric() -> None:
    """PowerAnalyzer computes Bernoulli variance p*(1-p) for binary conversion/success rates."""
    values = [1.0] * 300 + [0.0] * 300  # 50% conversion, n=600
    baseline = BaselineData(metric_name="conversion_rate", values=values)

    small_effect = PowerAnalyzer().analyze(baseline, effect_size_percent=5.0)
    large_effect = PowerAnalyzer().analyze(baseline, effect_size_percent=25.0)

    assert small_effect.sufficient is False
    assert large_effect.sufficient is True
    assert large_effect.current_power > small_effect.current_power


@pytest.mark.unit
def test_mde_decreases_as_sample_size_increases() -> None:
    """Larger baseline sample sizes tighten the Minimum Detectable Effect."""
    rng = np.random.default_rng(99)
    small = BaselineData(metric_name="latency_ms", values=list(rng.normal(100.0, 15.0, 50)))
    large = BaselineData(metric_name="latency_ms", values=list(rng.normal(100.0, 15.0, 500)))

    analyzer = PowerAnalyzer(target_power=0.80)
    mde_small = analyzer.analyze(small).minimum_detectable_effect_percent
    mde_large = analyzer.analyze(large).minimum_detectable_effect_percent

    assert mde_large < mde_small


@pytest.mark.unit
def test_one_sided_alternative_requires_fewer_samples_than_two_sided() -> None:
    """One-sided test requires smaller sample size than two-sided test at identical alpha/power."""
    rng = np.random.default_rng(5)
    baseline = BaselineData(metric_name="latency_ms", values=list(rng.normal(100.0, 15.0, 150)))

    two_sided = PowerAnalyzer(alternative="two-sided").analyze(baseline, effect_size_percent=10.0)
    one_sided = PowerAnalyzer(alternative="greater").analyze(baseline, effect_size_percent=10.0)

    assert one_sided.recommended_sample_size < two_sided.recommended_sample_size
    assert one_sided.current_power > two_sided.current_power


@pytest.mark.unit
def test_tiny_sample_under_4_returns_underpowered_safely() -> None:
    """Baselines with fewer than 4 samples return sufficient=False cleanly without NaN."""
    baseline = BaselineData(metric_name="latency_ms", values=[100.0, 105.0, 102.0])
    analysis = PowerAnalyzer().analyze(baseline)

    assert analysis.sufficient is False
    assert analysis.current_power == 0.0
    assert analysis.recommended_sample_size >= 60


@pytest.mark.unit
def test_power_analyzer_rejects_invalid_hyperparameters() -> None:
    """Invalid target_power, alpha, or alternative values raise ValueError."""
    with pytest.raises(ValueError, match="target_power"):
        PowerAnalyzer(target_power=0.4)
    with pytest.raises(ValueError, match="alpha"):
        PowerAnalyzer(alpha=0.8)
    with pytest.raises(ValueError, match="alternative"):
        PowerAnalyzer(alternative="invalid")
