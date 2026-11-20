"""
Statistical quality validator for pre-experiment baselines (EVO-019).

Checks sample size sufficiency, coefficient of variation (CV), Z-score outliers,
distribution normality (Shapiro-Wilk / KS), and constant-value anomalies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.stats import kstest, shapiro, zscore

from product_value_system.validation.baseline import BaselineData


@dataclass
class ValidationResult:
    """Outcome of statistical baseline validation checks."""

    valid: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize validation result to dictionary."""
        return {
            "valid": self.valid,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "suggestions": list(self.suggestions),
            "metrics": dict(self.metrics),
        }


class StatisticalValidator:
    """Validate statistical properties of baseline observations prior to experimentation."""

    def __init__(
        self,
        min_sample_size: int = 30,
        max_cv: float = 0.3,
        outlier_threshold: float = 3.0,
    ) -> None:
        self.min_sample_size = min_sample_size
        self.max_cv = max_cv
        self.outlier_threshold = outlier_threshold

    def validate(self, baseline: BaselineData) -> ValidationResult:
        """Execute all statistical checks on ``baseline``."""
        warnings: list[str] = []
        errors: list[str] = []
        suggestions: list[str] = []
        metrics: dict[str, float] = {}

        # 1. Sample size check
        sample_check = self._check_sample_size(baseline)
        if not sample_check["valid"]:
            errors.append(str(sample_check["message"]))
            suggestions.append(str(sample_check["suggestion"]))
        metrics["sample_size"] = float(baseline.sample_size)
        metrics["mean"] = round(baseline.mean, 6)
        metrics["std"] = round(baseline.std, 6)

        # 2. Constant / empty values check
        if baseline.sample_size == 0:
            errors.append("Baseline contains zero observations")
            suggestions.append("Verify telemetry query window and filters")
        elif len(set(baseline.values)) == 1:
            errors.append("All baseline values are identical (no variance)")
            suggestions.append("Verify metric is being collected correctly")

        # 3. Variance / CV check (binary conversion/success rates naturally have larger CV)
        variance_check = self._check_variance(baseline)
        if not variance_check["valid"]:
            errors.append(str(variance_check["message"]))
            suggestions.append(str(variance_check["suggestion"]))
        metrics["cv"] = round(baseline.cv, 6) if np.isfinite(baseline.cv) else float("inf")

        # 4. Z-score outlier detection
        outlier_check = self._check_outliers(baseline)
        if int(outlier_check["count"]) > 0:
            warnings.append(str(outlier_check["message"]))
            suggestions.append(str(outlier_check["suggestion"]))
        metrics["outlier_count"] = float(outlier_check["count"])
        metrics["outlier_percentage"] = round(float(outlier_check["percentage"]), 4)

        # 5. Normality test (informational warning for continuous data)
        normality_check = self._check_normality(baseline)
        if not normality_check["normal"]:
            warnings.append(str(normality_check["message"]))
        metrics["normality_p_value"] = round(float(normality_check["p_value"]), 6)

        return ValidationResult(
            valid=len(errors) == 0,
            warnings=warnings,
            errors=errors,
            suggestions=suggestions,
            metrics=metrics,
        )

    def _check_sample_size(self, baseline: BaselineData) -> dict[str, Any]:
        valid = baseline.sample_size >= self.min_sample_size
        deficit = max(0, self.min_sample_size - baseline.sample_size)
        return {
            "valid": valid,
            "message": (
                f"Insufficient samples: {baseline.sample_size} (minimum: {self.min_sample_size})"
                if not valid
                else ""
            ),
            "suggestion": f"Collect {deficit} more samples" if not valid else "",
        }

    def _check_variance(self, baseline: BaselineData) -> dict[str, Any]:
        if baseline.sample_size < 2 or baseline.is_binary:
            return {"valid": True, "message": "", "suggestion": ""}
        valid = baseline.cv <= self.max_cv
        return {
            "valid": valid,
            "message": (
                f"High variance: CV={baseline.cv:.2f} (maximum: {self.max_cv:.2f})"
                if not valid
                else ""
            ),
            "suggestion": (
                "High variance may indicate an unstable baseline. Consider: "
                "(1) collecting more samples, (2) investigating root cause, "
                "(3) using median/percentiles instead of mean"
                if not valid
                else ""
            ),
        }

    def _check_outliers(self, baseline: BaselineData) -> dict[str, Any]:
        if baseline.sample_size < 3 or baseline.std <= 1e-12 or baseline.is_binary:
            return {"count": 0, "percentage": 0.0, "message": "", "suggestion": ""}

        z_scores = np.abs(zscore(np.asarray(baseline.values, dtype=float)))
        outlier_count = int(np.sum(z_scores > self.outlier_threshold))
        outlier_pct = (outlier_count / baseline.sample_size) * 100.0
        return {
            "count": outlier_count,
            "percentage": outlier_pct,
            "message": (
                f"Detected {outlier_count} outliers ({outlier_pct:.1f}% of samples)"
                if outlier_count > 0
                else ""
            ),
            "suggestion": (
                "Consider trimming outliers or investigating cause "
                "(infrastructure spikes, malformed requests)"
                if outlier_count > 0
                else ""
            ),
        }

    def _check_normality(self, baseline: BaselineData) -> dict[str, Any]:
        if baseline.sample_size < 3 or baseline.std <= 1e-12 or baseline.is_binary:
            return {"normal": True, "p_value": 1.0, "message": ""}

        arr = np.asarray(baseline.values, dtype=float)
        try:
            if len(arr) <= 5000:
                _, p_value = shapiro(arr)
            else:
                standardized = (arr - baseline.mean) / baseline.std
                _, p_value = kstest(standardized, "norm")
            p_val = float(p_value)
            normal = p_val > 0.05
            return {
                "normal": normal,
                "p_value": p_val,
                "message": (
                    f"Baseline not normally distributed (p={p_val:.3f}). "
                    "Consider using non-parametric tests (e.g., Mann-Whitney U)."
                    if not normal
                    else ""
                ),
            }
        except Exception:
            return {"normal": True, "p_value": 1.0, "message": ""}
