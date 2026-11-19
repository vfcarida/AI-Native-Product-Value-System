"""
Statistical and quality validation for customer simulation results (EVO-009).

Compares simulated customer distributions against real empirical survey data
using Welch's two-sample t-test and two-sample Kolmogorov-Smirnov (KS) test,
checks sample size and quality thresholds from ``validation_criteria.yaml``,
and computes a calibrated confidence score.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from scipy import stats

from product_value_system.simulation.tinytroupe_adapter import SimulationResult


@dataclass
class StatisticalTestResult:
    """Result of comparing simulated vs. empirical distributions."""

    test_name: str
    statistic: float
    p_value: float
    threshold_p_value: float
    passed: bool
    interpretation: str


@dataclass
class ValidationReport:
    """Comprehensive simulation validity and confidence report."""

    passed: bool
    confidence_score: float
    confidence_level: str  # "high", "medium", "low"
    quality_checks_passed: dict[str, bool] = field(default_factory=dict)
    statistical_tests: list[StatisticalTestResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize report to dictionary."""
        return {
            "passed": self.passed,
            "confidence_score": self.confidence_score,
            "confidence_level": self.confidence_level,
            "quality_checks_passed": self.quality_checks_passed,
            "statistical_tests": [
                {
                    "test_name": t.test_name,
                    "statistic": t.statistic,
                    "p_value": t.p_value,
                    "threshold_p_value": t.threshold_p_value,
                    "passed": t.passed,
                    "interpretation": t.interpretation,
                }
                for t in self.statistical_tests
            ],
            "warnings": self.warnings,
        }


class SimulationValidator:
    """
    Validates simulation quality, sample adequacy, and empirical alignment.
    """

    def __init__(
        self,
        criteria_path: Path | str | None = None,
    ) -> None:
        self.criteria = self._load_criteria(criteria_path)

    def _load_criteria(self, criteria_path: Path | str | None) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "minimum_population_size": 50,
            "minimum_interactions_per_persona": 3,
            "t_test_p_value": 0.05,
            "ks_test_p_value": 0.05,
            "quality_thresholds": {
                "persona_adherence": 0.80,
                "interaction_realism": 0.70,
                "response_diversity": 0.60,
            },
            "confidence_scoring": {
                "high": 0.80,
                "medium": 0.60,
                "low": 0.40,
            },
        }
        if criteria_path is None:
            return defaults

        path = Path(criteria_path)
        if not path.exists():
            return defaults

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        val = raw.get("validation", {})
        defaults["minimum_population_size"] = int(
            val.get("minimum_population_size", defaults["minimum_population_size"])
        )
        defaults["minimum_interactions_per_persona"] = int(
            val.get(
                "minimum_interactions_per_persona",
                defaults["minimum_interactions_per_persona"],
            )
        )

        for test_cfg in val.get("statistical_tests", []):
            name = test_cfg.get("name")
            threshold = test_cfg.get("threshold", {}).get("p_value", 0.05)
            if name == "t_test":
                defaults["t_test_p_value"] = float(threshold)
            elif name == "ks_test":
                defaults["ks_test_p_value"] = float(threshold)

        q_thresholds: dict[str, float] = dict(defaults["quality_thresholds"])
        for check in val.get("quality_checks", []):
            if isinstance(check, dict):
                for metric_name, min_val in check.items():
                    q_thresholds[str(metric_name)] = float(min_val)
        defaults["quality_thresholds"] = q_thresholds

        if "confidence_scoring" in val:
            defaults["confidence_scoring"].update(val["confidence_scoring"])

        return defaults

    def compare_distributions(
        self,
        simulated_scores: list[float],
        real_scores: list[float],
    ) -> list[StatisticalTestResult]:
        """
        Compare simulated numeric ratings against empirical real-world survey ratings.

        A p-value >= threshold indicates we fail to reject the null hypothesis that
        simulated and real samples come from the same mean / distribution.
        """
        if len(simulated_scores) < 2 or len(real_scores) < 2:
            raise ValueError("Both simulated_scores and real_scores require >= 2 samples")

        t_thresh = float(self.criteria["t_test_p_value"])
        ks_thresh = float(self.criteria["ks_test_p_value"])

        t_stat_raw, t_pval_raw = stats.ttest_ind(
            simulated_scores, real_scores, equal_var=False
        )
        t_stat = float(t_stat_raw)
        t_pval = float(t_pval_raw)
        t_passed = t_pval >= t_thresh

        ks_result = stats.ks_2samp(simulated_scores, real_scores)
        ks_stat = float(ks_result.statistic)
        ks_pval = float(ks_result.pvalue)
        ks_passed = ks_pval >= ks_thresh

        return [
            StatisticalTestResult(
                test_name="t_test",
                statistic=round(t_stat, 4),
                p_value=round(t_pval, 4),
                threshold_p_value=t_thresh,
                passed=t_passed,
                interpretation=(
                    "Simulated mean is statistically consistent with real data"
                    if t_passed
                    else "Simulated mean differs significantly from real data"
                ),
            ),
            StatisticalTestResult(
                test_name="ks_test",
                statistic=round(ks_stat, 4),
                p_value=round(ks_pval, 4),
                threshold_p_value=ks_thresh,
                passed=ks_passed,
                interpretation=(
                    "Simulated distribution matches empirical distribution"
                    if ks_passed
                    else "Simulated distribution diverges from empirical data"
                ),
            ),
        ]

    def validate(
        self,
        result: SimulationResult,
        *,
        simulated_scores: list[float] | None = None,
        real_scores: list[float] | None = None,
    ) -> ValidationReport:
        """
        Validate a ``SimulationResult`` against quality, sample-size, and optional empirical checks.
        """
        warnings: list[str] = []
        min_pop = int(self.criteria["minimum_population_size"])
        min_interactions = int(self.criteria["minimum_interactions_per_persona"])

        if result.population_size < min_pop:
            warnings.append(
                f"Population size {result.population_size} is below recommended minimum {min_pop}"
            )
        if len(result.interactions) < min_interactions:
            warnings.append(
                f"Interaction count {len(result.interactions)} is below recommended "
                f"minimum {min_interactions}"
            )

        quality_thresholds: dict[str, float] = self.criteria["quality_thresholds"]
        quality_checks_passed: dict[str, bool] = {}
        for metric, min_score in quality_thresholds.items():
            actual = float(result.quality_metrics.get(metric, 0.0))
            quality_checks_passed[metric] = actual >= min_score

        stat_tests: list[StatisticalTestResult] = []
        if simulated_scores is not None and real_scores is not None:
            stat_tests = self.compare_distributions(simulated_scores, real_scores)

        # Compute weighted confidence score in [0.0, 1.0]
        q = result.quality_metrics
        quality_score = (
            0.4 * float(q.get("persona_adherence", 0.0))
            + 0.3 * float(q.get("interaction_realism", 0.0))
            + 0.3 * float(q.get("response_diversity", 0.0))
        )

        if stat_tests:
            empirical_factor = sum(1.0 for t in stat_tests if t.passed) / len(stat_tests)
            confidence_score = round(0.6 * quality_score + 0.4 * empirical_factor, 3)
        else:
            confidence_score = round(quality_score, 3)

        scoring = self.criteria["confidence_scoring"]
        if confidence_score >= float(scoring.get("high", 0.80)):
            confidence_level = "high"
        elif confidence_score >= float(scoring.get("medium", 0.60)):
            confidence_level = "medium"
        else:
            confidence_level = "low"

        all_quality_ok = all(quality_checks_passed.values())
        all_stats_ok = all(t.passed for t in stat_tests) if stat_tests else True
        passed = all_quality_ok and all_stats_ok

        return ValidationReport(
            passed=passed,
            confidence_score=confidence_score,
            confidence_level=confidence_level,
            quality_checks_passed=quality_checks_passed,
            statistical_tests=stat_tests,
            warnings=warnings,
        )
