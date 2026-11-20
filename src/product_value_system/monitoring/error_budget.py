"""
Error budget tracking, policy evaluation, and operational action rules (EVO-014).
"""

from dataclasses import dataclass, field
from typing import Any

from product_value_system.monitoring.slo import SLO

DEFAULT_ERROR_BUDGET_ACTIONS: dict[float, str] = {
    0.50: "Continue normal operations",
    0.25: "Suspend non-critical changes, focus on reliability",
    0.00: "Freeze all changes except emergency fixes",
}


@dataclass
class ErrorBudgetStatus:
    """Snapshot of an SLO's current compliance and remaining error budget."""

    slo_name: str
    target: float
    actual: float
    error_budget: float
    budget_remaining: float
    is_met: bool
    burn_rate: float
    recommended_action: str
    severity: str  # "ok", "warning", "critical"

    def to_dict(self) -> dict[str, Any]:
        """Serialize error budget status to dictionary."""
        return {
            "slo_name": self.slo_name,
            "target": self.target,
            "actual": round(self.actual, 6),
            "error_budget": self.error_budget,
            "budget_remaining": round(self.budget_remaining, 4),
            "is_met": self.is_met,
            "burn_rate": round(self.burn_rate, 4),
            "recommended_action": self.recommended_action,
            "severity": self.severity,
        }


@dataclass
class ErrorBudgetPolicy:
    """
    Defines automated operational actions triggered as an SLO's error budget
    depletes (e.g., at 50%, 25%, and 0% remaining).
    """

    slo: SLO
    actions: dict[float, str] = field(
        default_factory=lambda: dict(DEFAULT_ERROR_BUDGET_ACTIONS)
    )

    def get_action(self, budget_remaining: float) -> str:
        """
        Determine the governing policy action for ``budget_remaining`` in ``[0.0, 1.0]``.

        Threshold interpretation:
        - ``budget_remaining <= 0.0`` -> ``actions[0.0]`` (freeze/rollback)
        - ``budget_remaining >= max_threshold`` -> ``actions[max_threshold]`` (normal)
        - Intermediate values map to the active warning band threshold.
        """
        if not self.actions:
            return "Continue normal operations"

        ordered = sorted(self.actions.keys())
        if budget_remaining <= ordered[0]:
            return self.actions[ordered[0]]
        if budget_remaining >= ordered[-1]:
            return self.actions[ordered[-1]]

        # Find the lowest non-zero band threshold >= budget_remaining, falling back
        # to the nearest positive threshold so e.g. 0.20 maps to the 0.25 band
        # rather than 0.00 (exhausted).
        positive = [t for t in ordered if t > 0.0]
        for threshold in positive:
            if budget_remaining <= threshold:
                return self.actions[threshold]
        return self.actions[positive[-1]] if positive else self.actions[ordered[0]]

    def evaluate(
        self,
        actual: float,
        *,
        window_error_rate: float | None = None,
    ) -> ErrorBudgetStatus:
        """Evaluate ``actual`` SLI value against ``self.slo`` and policy thresholds."""
        remaining = self.slo.compute_budget_remaining(actual)
        met = self.slo.is_met(actual)
        error_budget = self.slo.compute_error_budget()

        if window_error_rate is not None:
            burn_rate = self.slo.compute_burn_rate(window_error_rate)
        elif self.slo.sli.higher_is_better:
            burn_rate = self.slo.compute_burn_rate(max(0.0, 1.0 - actual))
        else:
            burn_rate = self.slo.compute_burn_rate(max(0.0, actual - self.slo.target))

        if remaining <= 0.0:
            severity = "critical"
        elif remaining < 0.50:
            severity = "warning"
        else:
            severity = "ok"

        return ErrorBudgetStatus(
            slo_name=self.slo.name,
            target=self.slo.target,
            actual=actual,
            error_budget=error_budget,
            budget_remaining=remaining,
            is_met=met,
            burn_rate=burn_rate,
            recommended_action=self.get_action(remaining),
            severity=severity,
        )
