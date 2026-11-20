"""
Statistical power analysis and sample size calculators for A/B experiments (EVO-016).
"""

from __future__ import annotations

import math

from scipy.stats import norm

from product_value_system.experiment.models import ExperimentHypothesis


class PowerAnalyzer:
    """
    Computes required per-variant sample sizes and experiment durations
    using standard Cohen (1988) / Kohavi et al. (2020) power formulas.
    """

    @staticmethod
    def compute_ttest_sample_size(
        baseline_value: float,
        minimum_detectable_effect: float,
        *,
        confidence_level: float = 0.95,
        statistical_power: float = 0.80,
        std_dev: float | None = None,
    ) -> int:
        """
        Compute required sample size per variant for a two-sample t-test.

        Formula:
            ``n = ceil(2 * (z_alpha + z_beta)^2 * (sigma^2 / delta^2))``

        Args:
            baseline_value: Baseline mean metric value
            minimum_detectable_effect: Minimum meaningful relative lift (e.g., 0.05 = 5%)
            confidence_level: Confidence level (1 - alpha), default 0.95
            statistical_power: Statistical power (1 - beta), default 0.80
            std_dev: Optional known standard deviation (defaults to ``0.1 * |baseline|``)
        """
        if minimum_detectable_effect <= 0.0:
            raise ValueError("minimum_detectable_effect must be positive")
        if not 0.5 < confidence_level < 1.0:
            raise ValueError("confidence_level must be in (0.5, 1.0)")
        if not 0.5 < statistical_power < 1.0:
            raise ValueError("statistical_power must be in (0.5, 1.0)")

        z_alpha = float(norm.ppf(1.0 - (1.0 - confidence_level) / 2.0))
        z_beta = float(norm.ppf(statistical_power))

        base_scale = abs(baseline_value) if abs(baseline_value) > 1e-9 else 1.0
        sigma = float(std_dev) if std_dev is not None else base_scale * 0.1
        delta = minimum_detectable_effect * base_scale

        if sigma <= 0.0 or delta <= 0.0:
            raise ValueError("Standard deviation and absolute effect size must be positive")

        n = 2.0 * ((z_alpha + z_beta) ** 2) * (sigma**2) / (delta**2)
        return max(2, int(math.ceil(n)))

    @staticmethod
    def compute_proportion_sample_size(
        baseline_rate: float,
        minimum_detectable_effect: float,
        *,
        confidence_level: float = 0.95,
        statistical_power: float = 0.80,
    ) -> int:
        """
        Compute required sample size per variant for a two-proportion chi-square / z-test.

        Args:
            baseline_rate: Baseline conversion rate in ``(0, 1)``
            minimum_detectable_effect: Relative lift (e.g., 0.10 = 10% relative lift)
            confidence_level: Confidence level (1 - alpha), default 0.95
            statistical_power: Statistical power (1 - beta), default 0.80
        """
        if not 0.0 < baseline_rate < 1.0:
            raise ValueError(f"baseline_rate must be in (0, 1), got {baseline_rate}")
        if minimum_detectable_effect <= 0.0:
            raise ValueError("minimum_detectable_effect must be positive")

        p1 = baseline_rate
        p2 = min(0.9999, max(0.0001, p1 * (1.0 + minimum_detectable_effect)))
        delta = abs(p2 - p1)
        if delta <= 1e-9:
            raise ValueError("Effective difference between proportions must be non-zero")

        z_alpha = float(norm.ppf(1.0 - (1.0 - confidence_level) / 2.0))
        z_beta = float(norm.ppf(statistical_power))
        p_pooled = (p1 + p2) / 2.0

        numerator = (
            z_alpha * math.sqrt(2.0 * p_pooled * (1.0 - p_pooled))
            + z_beta * math.sqrt(p1 * (1.0 - p1) + p2 * (1.0 - p2))
        ) ** 2
        n = numerator / (delta**2)
        return max(2, int(math.ceil(n)))

    @classmethod
    def compute_sample_size(
        cls,
        hypothesis: ExperimentHypothesis,
        *,
        std_dev: float | None = None,
    ) -> int:
        """Dispatch sample size calculation based on ``hypothesis.metric_type``."""
        if hypothesis.metric_type == "proportion" and 0.0 < hypothesis.baseline_value < 1.0:
            return cls.compute_proportion_sample_size(
                baseline_rate=hypothesis.baseline_value,
                minimum_detectable_effect=hypothesis.minimum_detectable_effect,
                confidence_level=hypothesis.confidence_level,
                statistical_power=hypothesis.statistical_power,
            )
        return cls.compute_ttest_sample_size(
            baseline_value=hypothesis.baseline_value,
            minimum_detectable_effect=hypothesis.minimum_detectable_effect,
            confidence_level=hypothesis.confidence_level,
            statistical_power=hypothesis.statistical_power,
            std_dev=std_dev,
        )

    @staticmethod
    def estimate_duration_days(
        sample_size_per_variant: int,
        *,
        daily_traffic: int = 1000,
        treatment_allocation: float = 0.5,
        min_days: int = 7,
        max_days: int = 30,
    ) -> int:
        """
        Estimate required experiment duration in days, clamped to ``[min_days, max_days]``.
        """
        clamped_alloc = max(0.01, min(0.99, treatment_allocation))
        effective_daily = max(1.0, float(daily_traffic) * clamped_alloc)
        raw_days = int(math.ceil(sample_size_per_variant / effective_daily))
        return max(min_days, min(max_days, raw_days))
