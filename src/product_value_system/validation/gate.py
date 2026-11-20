"""
Pre-experiment baseline validation gate, 0-100 quality score, and
auto-validating experiment runner (EVO-019).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from product_value_system.validation.baseline import BaselineCollector, BaselineData
from product_value_system.validation.power_analyzer import PowerAnalysis, PowerAnalyzer
from product_value_system.validation.stability_checker import StabilityChecker, StabilityResult
from product_value_system.validation.statistical_validator import (
    StatisticalValidator,
    ValidationResult,
)

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    """Aggregate pre-experiment baseline validation gate verdict and 0-100 quality score."""

    passed: bool
    can_proceed_with_warning: bool
    quality_score: float  # 0.0 - 100.0 dashboard score
    validation: ValidationResult
    power_analysis: PowerAnalysis
    stability: StabilityResult
    summary: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize gate verdict to JSON/YAML dictionary."""
        return {
            "passed": self.passed,
            "can_proceed_with_warning": self.can_proceed_with_warning,
            "quality_score": round(self.quality_score, 1),
            "validation": self.validation.to_dict(),
            "power_analysis": self.power_analysis.to_dict(),
            "stability": self.stability.to_dict(),
            "summary": self.summary,
        }


class ValidationGate:
    """Blocks or warns on experiments backed by statistically invalid or unstable baselines."""

    def __init__(
        self,
        collector: BaselineCollector | None = None,
        validator: StatisticalValidator | None = None,
        power_analyzer: PowerAnalyzer | None = None,
        stability_checker: StabilityChecker | None = None,
    ) -> None:
        self.collector = collector or BaselineCollector()
        self.validator = validator or StatisticalValidator()
        self.power_analyzer = power_analyzer or PowerAnalyzer()
        self.stability_checker = stability_checker

    def validate_baseline(
        self,
        baseline: BaselineData,
        effect_size_percent: float = 10.0,
        strict: bool = True,
    ) -> GateResult:
        """Run statistical, power, and stability checks and compute 0-100 quality score."""
        validation = self.validator.validate(baseline)
        power_analysis = self.power_analyzer.analyze(
            baseline,
            effect_size_percent=effect_size_percent,
        )
        if self.stability_checker is not None:
            stability = self.stability_checker.check_stability(baseline)
        else:
            stability = StabilityChecker().check_stability(baseline)

        if not power_analysis.sufficient:
            deficit = max(0, power_analysis.recommended_sample_size - baseline.sample_size)
            if deficit > 0:
                msg = (
                    f"Collect {deficit} more samples to reach "
                    f"power={power_analysis.target_power:.2f}"
                )
                if msg not in validation.suggestions:
                    validation.suggestions.append(msg)

        quality_score = self._compute_quality_score(baseline, validation, power_analysis, stability)
        passed = validation.valid and power_analysis.sufficient and stability.stable
        can_proceed_with_warning = not strict and validation.valid and len(validation.errors) == 0

        summary = self._generate_summary(
            validation=validation,
            power=power_analysis,
            stability=stability,
            quality_score=quality_score,
            passed=passed,
        )
        return GateResult(
            passed=passed,
            can_proceed_with_warning=can_proceed_with_warning,
            quality_score=quality_score,
            validation=validation,
            power_analysis=power_analysis,
            stability=stability,
            summary=summary,
        )

    def _compute_quality_score(
        self,
        baseline: BaselineData,
        validation: ValidationResult,
        power: PowerAnalysis,
        stability: StabilityResult,
    ) -> float:
        """Compute interpretable 0-100 baseline quality score across 5 pillars."""
        # 1. Sample size (25 pts)
        min_n = max(1, self.validator.min_sample_size)
        sample_pts = 25.0 * min(1.0, baseline.sample_size / min_n)

        # 2. Variance / CV health (25 pts)
        if baseline.sample_size < 2 or len(set(baseline.values)) <= 1:
            variance_pts = 0.0
        elif baseline.is_binary or baseline.cv <= self.validator.max_cv:
            variance_pts = 25.0
        else:
            ratio = self.validator.max_cv / max(baseline.cv, 1e-9)
            variance_pts = 25.0 * max(0.0, min(1.0, ratio))

        # 3. Power sufficiency (25 pts)
        power_ratio = power.current_power / max(power.target_power, 1e-9)
        power_pts = 25.0 * max(0.0, min(1.0, power_ratio))

        # 4. Stability (15 pts)
        stability_pts = 15.0
        if stability.drift_detected:
            stability_pts -= 7.0
        if stability.incidents_during_baseline:
            stability_pts -= 5.0
        if stability.deploys_during_baseline:
            stability_pts -= 3.0
        stability_pts = max(0.0, stability_pts)

        # 5. Outlier cleanliness (10 pts)
        outlier_pct = validation.metrics.get("outlier_percentage", 0.0)
        outlier_pts = max(0.0, 10.0 - outlier_pct)

        raw_total = sample_pts + variance_pts + power_pts + stability_pts + outlier_pts
        clamped = max(0.0, min(100.0, raw_total))
        if validation.errors:
            clamped = min(clamped, 45.0)
        return round(clamped, 1)

    def _generate_summary(
        self,
        *,
        validation: ValidationResult,
        power: PowerAnalysis,
        stability: StabilityResult,
        quality_score: float,
        passed: bool,
    ) -> str:
        lines: list[str] = []
        verdict = "PASSED" if passed else "FAILED"
        lines.append(f"Baseline validation {verdict} (Quality Score: {quality_score:.1f}/100)")
        lines.append("")
        lines.append("Statistical Validation:")
        outliers = int(validation.metrics.get("outlier_count", 0))
        lines.append(f"  Sample size:              {int(validation.metrics.get('sample_size', 0))}")
        lines.append(f"  Coefficient of variation: {validation.metrics.get('cv', 0.0):.2f}")
        lines.append(f"  Outliers:                 {outliers}")

        if validation.errors:
            lines.append("\nErrors:")
            for error in validation.errors:
                lines.append(f"  - {error}")

        all_warnings = list(validation.warnings) + list(stability.warnings)
        if all_warnings:
            lines.append("\nWarnings:")
            for warning in all_warnings:
                lines.append(f"  - {warning}")

        lines.append("\nPower Analysis:")
        lines.append(f"  Current power:            {power.current_power:.2f}")
        lines.append(f"  Target power:             {power.target_power:.2f}")
        lines.append(f"  MDE:                      {power.minimum_detectable_effect_percent:.1f}%")
        lines.append(f"  Recommended sample size:  {power.recommended_sample_size}")
        lines.append(f"  Sufficient:               {'Yes' if power.sufficient else 'No'}")

        lines.append("\nStability:")
        lines.append(f"  Stable:                   {'Yes' if stability.stable else 'No'}")
        lines.append(f"  Drift detected:           {'Yes' if stability.drift_detected else 'No'}")
        lines.append(f"  Incidents:                {len(stability.incidents_during_baseline)}")
        lines.append(f"  Deploys:                  {len(stability.deploys_during_baseline)}")

        if validation.suggestions:
            lines.append("\nSuggestions:")
            for suggestion in validation.suggestions:
                lines.append(f"  - {suggestion}")

        return "\n".join(lines)


class ValidatedExperimentRunner:
    """
    Wrapper starting EVO-016 ``ABTest`` experiments only after verifying baseline
    quality via ``ValidationGate``.
    """

    def __init__(self, validation_gate: ValidationGate | None = None) -> None:
        self.validation_gate = validation_gate or ValidationGate()
        self.last_gate_result: GateResult | None = None

    def start_experiment(
        self,
        ab_test: Any,
        baseline: BaselineData,
        *,
        effect_size_percent: float = 10.0,
        auto_validate: bool = True,
        strict_validation: bool = True,
    ) -> GateResult | None:
        """Validate ``baseline`` and start ``ab_test`` if gate passes (or warns in non-strict)."""
        if auto_validate:
            gate_result = self.validation_gate.validate_baseline(
                baseline,
                effect_size_percent=effect_size_percent,
                strict=strict_validation,
            )
            self.last_gate_result = gate_result
            if not gate_result.passed:
                if not gate_result.can_proceed_with_warning:
                    raise ValueError(f"Baseline validation failed:\n{gate_result.summary}")
                logger.warning("Baseline validation passed with warnings:\n%s", gate_result.summary)
            ab_test.start()
            return gate_result

        ab_test.start()
        return None
