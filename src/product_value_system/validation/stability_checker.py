"""
Baseline stability checker detecting temporal distribution drift (Mann-Whitney U),
production incidents, and deployments overlapping baseline collection (EVO-019).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scipy.stats import mannwhitneyu

from product_value_system.validation.baseline import BaselineData


@dataclass
class StabilityResult:
    """Outcome of baseline temporal stability and operational event checks."""

    stable: bool
    drift_detected: bool
    incidents_during_baseline: list[str] = field(default_factory=list)
    deploys_during_baseline: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    drift_p_value: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize stability check result to dictionary."""
        return {
            "stable": self.stable,
            "drift_detected": self.drift_detected,
            "drift_p_value": round(self.drift_p_value, 6),
            "incidents_during_baseline": list(self.incidents_during_baseline),
            "deploys_during_baseline": list(self.deploys_during_baseline),
            "warnings": list(self.warnings),
        }


class StabilityChecker:
    """Detect non-stationary baselines, incidents, and deployments during baseline window."""

    def __init__(
        self,
        incident_tracker: Any | None = None,
        deploy_tracker: Any | None = None,
    ) -> None:
        self.incident_tracker = incident_tracker
        self.deploy_tracker = deploy_tracker

    def check_stability(
        self,
        baseline: BaselineData,
        drift_sensitivity: float = 0.05,
    ) -> StabilityResult:
        """Evaluate baseline stationarity and overlapping operational events."""
        warnings: list[str] = []

        drift_detected, p_value = self._detect_drift(baseline, drift_sensitivity)
        if drift_detected:
            warnings.append(
                f"Baseline shows temporal drift (Mann-Whitney U p={p_value:.4f} < "
                f"{drift_sensitivity:.2f}). Consider using a shorter or more recent window."
            )

        incidents = self._check_incidents(baseline)
        if incidents:
            warnings.append(
                f"Detected {len(incidents)} incident(s) during baseline collection "
                f"({', '.join(incidents)}). Baseline may not represent normal behavior."
            )

        deploys = self._check_deploys(baseline)
        if deploys:
            warnings.append(
                f"Detected {len(deploys)} deployment(s) during baseline collection "
                f"({', '.join(deploys)}). System behavior may have shifted mid-window."
            )

        stable = not drift_detected and len(incidents) == 0 and len(deploys) == 0
        return StabilityResult(
            stable=stable,
            drift_detected=drift_detected,
            incidents_during_baseline=incidents,
            deploys_during_baseline=deploys,
            warnings=warnings,
            drift_p_value=p_value,
        )

    def _detect_drift(
        self,
        baseline: BaselineData,
        sensitivity: float,
    ) -> tuple[bool, float]:
        """Compare first half vs second half of baseline using Mann-Whitney U test."""
        if baseline.sample_size < 20 or len(set(baseline.values)) <= 1:
            return False, 1.0

        # Order chronologically if timestamps align with values
        if len(baseline.timestamps) == len(baseline.values):
            paired = sorted(
                zip(baseline.timestamps, baseline.values, strict=True),
                key=lambda p: p[0],
            )
            ordered_vals = [val for _, val in paired]
        else:
            ordered_vals = list(baseline.values)

        midpoint = len(ordered_vals) // 2
        first_half = ordered_vals[:midpoint]
        second_half = ordered_vals[midpoint:]

        _, p_value = mannwhitneyu(first_half, second_half, alternative="two-sided")
        p_val = float(p_value)
        return p_val < sensitivity, p_val

    def _check_incidents(self, baseline: BaselineData) -> list[str]:
        if self.incident_tracker is None or not baseline.timestamps:
            return []
        try:
            incidents = self.incident_tracker.query(
                start_time=min(baseline.timestamps),
                end_time=max(baseline.timestamps),
                severity=["critical", "high"],
            )
            return [
                str(inc["id"] if isinstance(inc, dict) else getattr(inc, "id", inc))
                for inc in incidents
            ]
        except Exception:
            return []

    def _check_deploys(self, baseline: BaselineData) -> list[str]:
        if self.deploy_tracker is None or not baseline.timestamps:
            return []
        try:
            deploys = self.deploy_tracker.query(
                start_time=min(baseline.timestamps),
                end_time=max(baseline.timestamps),
            )
            return [
                str(dep["id"] if isinstance(dep, dict) else getattr(dep, "id", dep))
                for dep in deploys
            ]
        except Exception:
            return []
