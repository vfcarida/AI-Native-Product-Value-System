"""
Statistical power analysis for baseline validation (EVO-019).

Calculates Minimum Detectable Effect (MDE), achieved statistical power using the
non-central t-distribution, and recommended total sample size for continuous and
binary (conversion / success rate) metrics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from scipy import stats

from product_value_system.validation.baseline import BaselineData


@dataclass
class PowerAnalysis:
    """Statistical power analysis summary for a collected baseline."""

    minimum_detectable_effect: float  # MDE in absolute metric units
    minimum_detectable_effect_percent: float  # MDE as % relative to baseline mean
    recommended_sample_size: int  # Total recommended samples (both arms combined)
    current_power: float  # Achieved statistical power in [0.0, 1.0]
    target_power: float
    sufficient: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialize power analysis to dictionary."""
        return {
            "minimum_detectable_effect": round(self.minimum_detectable_effect, 6),
            "minimum_detectable_effect_percent": round(self.minimum_detectable_effect_percent, 4),
            "recommended_sample_size": int(self.recommended_sample_size),
            "current_power": round(self.current_power, 4),
            "target_power": round(self.target_power, 4),
            "sufficient": self.sufficient,
        }


class PowerAnalyzer:
    """Analyze statistical power and sample size sufficiency from baseline data."""

    def __init__(
        self,
        target_power: float = 0.8,
        alpha: float = 0.05,
        alternative: str = "two-sided",
    ) -> None:
        if not 0.5 < target_power < 1.0:
            raise ValueError(f"target_power must be in (0.5, 1.0), got {target_power}")
        if not 0.0 < alpha < 0.5:
            raise ValueError(f"alpha must be in (0.0, 0.5), got {alpha}")
        if alternative not in {"two-sided", "greater", "less"}:
            raise ValueError(f"alternative must be two-sided, greater, or less; got {alternative}")
        self.target_power = target_power
        self.alpha = alpha
        self.alternative = alternative

    def analyze(
        self,
        baseline: BaselineData,
        effect_size: float | None = None,
        effect_size_percent: float | None = None,
    ) -> PowerAnalysis:
        """
        Compute MDE, achieved power, and required total sample size for ``baseline``.

        Supports continuous metrics (latency, cost, tokens) as well as binary rates
        (success_rate, conversion_rate).
        """
        if baseline.sample_size < 4:
            return PowerAnalysis(
                minimum_detectable_effect=float("inf"),
                minimum_detectable_effect_percent=float("inf"),
                recommended_sample_size=60,
                current_power=0.0,
                target_power=self.target_power,
                sufficient=False,
            )

        mean_abs = abs(baseline.mean) if abs(baseline.mean) > 1e-12 else 1.0

        # Resolve expected absolute effect size
        if effect_size is not None:
            abs_effect = abs(float(effect_size))
        elif effect_size_percent is not None:
            abs_effect = mean_abs * (abs(float(effect_size_percent)) / 100.0)
        else:
            abs_effect = mean_abs * 0.10

        abs_effect = max(abs_effect, 1e-9)

        # Handle binary/proportion vs continuous dispersion safely (including zero-std fixtures)
        if baseline.is_binary and 0.0 < baseline.mean < 1.0:
            p = baseline.mean
            effective_std = math.sqrt(p * (1.0 - p))
        elif baseline.std > 1e-12:
            effective_std = baseline.std
        else:
            # Conservative 20% CV assumption when fixture has zero empirical variance
            effective_std = mean_abs * 0.20

        cohen_d = abs_effect / max(effective_std, 1e-9)

        mde_d = self._calculate_mde(baseline.sample_size)
        mde_absolute = mde_d * effective_std
        mde_percent = (mde_absolute / mean_abs) * 100.0

        current_power = self._calculate_power(baseline.sample_size, cohen_d)
        required_n = self._calculate_sample_size(cohen_d)
        sufficient = baseline.sample_size >= required_n and current_power >= self.target_power

        return PowerAnalysis(
            minimum_detectable_effect=mde_absolute,
            minimum_detectable_effect_percent=mde_percent,
            recommended_sample_size=required_n,
            current_power=current_power,
            target_power=self.target_power,
            sufficient=sufficient,
        )

    def _z_alpha(self) -> float:
        if self.alternative == "two-sided":
            return float(stats.norm.ppf(1.0 - self.alpha / 2.0))
        return float(stats.norm.ppf(1.0 - self.alpha))

    def _calculate_mde(self, sample_size: int) -> float:
        """Calculate minimum detectable Cohen's d for total sample size ``sample_size``."""
        z_alpha = self._z_alpha()
        z_beta = float(stats.norm.ppf(self.target_power))
        n_per_group = max(2.0, sample_size / 2.0)
        return (z_alpha + z_beta) * math.sqrt(2.0 / n_per_group)

    def _calculate_power(self, sample_size: int, effect_size: float) -> float:
        """Calculate statistical power using the non-central t-distribution."""
        n_per_group = max(2.0, sample_size / 2.0)
        df = max(1, sample_size - 2)
        ncp = abs(effect_size) * math.sqrt(n_per_group / 2.0)

        if self.alternative == "two-sided":
            t_crit = float(stats.t.ppf(1.0 - self.alpha / 2.0, df=df))
            upper = 1.0 - float(stats.nct.cdf(t_crit, df=df, nc=ncp))
            lower = float(stats.nct.cdf(-t_crit, df=df, nc=ncp))
            power = upper + lower
        else:
            t_crit = float(stats.t.ppf(1.0 - self.alpha, df=df))
            power = 1.0 - float(stats.nct.cdf(t_crit, df=df, nc=ncp))

        if math.isnan(power):
            return 0.0
        return max(0.0, min(1.0, power))

    def _calculate_sample_size(self, effect_size: float) -> int:
        """Calculate total sample size (both groups combined) for target power."""
        z_alpha = self._z_alpha()
        z_beta = float(stats.norm.ppf(self.target_power))
        safe_d = max(abs(effect_size), 1e-6)
        n_per_group = 2.0 * ((z_alpha + z_beta) / safe_d) ** 2
        return max(4, int(math.ceil(n_per_group * 2.0)))
