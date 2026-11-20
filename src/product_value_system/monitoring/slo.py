"""
Service Level Objective (SLO) definitions and error budget calculations (EVO-014).
"""

from dataclasses import dataclass
from typing import Any

from product_value_system.monitoring.sli import (
    AVAILABILITY_SLI,
    COST_PER_OUTCOME_SLI,
    ERROR_RATE_SLI,
    HALLUCINATION_FREE_SLI,
    LATENCY_P95_SLI,
    QUALITY_SCORE_SLI,
    SAFETY_SLI,
    SLI,
)


@dataclass(frozen=True)
class SLO:
    """
    Service Level Objective pairing an ``SLI`` with a target value and rolling window.

    Supports both higher-is-better ratio SLIs (availability, quality, hallucination-free)
    and lower-is-better bounded SLIs (latency p95, cost per outcome, error rate).
    """

    name: str
    sli: SLI
    target: float
    measurement_window: str = "30d"
    max_acceptable_value: float | None = None

    def compute_error_budget(self) -> float:
        """
        Compute allowable failure budget.

        - For higher-is-better ratio SLIs (e.g., target=0.999): ``1.0 - target`` (= 0.001)
        - For lower-is-better SLIs (e.g., latency<=5s or cost<=$0.50): headroom between
          ``target`` and ``max_acceptable_value`` (defaults to ``target`` for non-ratio
          metrics or ``target`` itself for error-rate ratios).
        """
        if self.sli.higher_is_better:
            return round(max(0.0, 1.0 - self.target), 10)
        if self.max_acceptable_value is not None and self.max_acceptable_value > self.target:
            return round(self.max_acceptable_value - self.target, 10)
        return round(max(self.target, 1e-9), 10)

    def compute_budget_remaining(self, actual: float) -> float:
        """
        Compute fraction of error budget remaining in ``[0.0, 1.0]``.

        Example (availability):
            Target = 0.99 (1% budget), Actual = 0.985 (0.5% consumed)
            Budget remaining = 1.0 - (0.005 / 0.01) = 0.50 (50% remaining).
        """
        error_budget = self.compute_error_budget()
        if error_budget <= 0.0:
            return 1.0 if self.is_met(actual) else 0.0

        if self.sli.higher_is_better:
            budget_consumed = self.target - actual
            if budget_consumed <= 0.0:
                return 1.0
            remaining = 1.0 - (budget_consumed / error_budget)
            return round(max(0.0, min(1.0, remaining)), 6)

        # Lower-is-better (latency, cost, error_rate)
        if actual <= self.target:
            return 1.0
        overshoot = actual - self.target
        remaining = 1.0 - (overshoot / error_budget)
        return round(max(0.0, min(1.0, remaining)), 6)

    def is_met(self, actual: float) -> bool:
        """Return True if ``actual`` satisfies the SLO target."""
        if self.sli.higher_is_better:
            return actual >= self.target
        return actual <= self.target

    def compute_burn_rate(self, window_error_rate: float) -> float:
        """Compute error-budget burn rate multiplier for a given failure rate."""
        budget = self.compute_error_budget()
        if budget <= 0.0:
            return float("inf") if window_error_rate > 0.0 else 0.0
        return round(window_error_rate / budget, 4)

    def to_dict(self) -> dict[str, Any]:
        """Serialize SLO definition to dictionary."""
        return {
            "name": self.name,
            "sli": self.sli.to_dict(),
            "target": self.target,
            "measurement_window": self.measurement_window,
            "error_budget": self.compute_error_budget(),
        }


# Production SLO catalog
AVAILABILITY_SLO = SLO(
    name="availability",
    sli=AVAILABILITY_SLI,
    target=0.99,  # 99% availability (1% error budget over 30d)
    measurement_window="30d",
)

LATENCY_SLO = SLO(
    name="latency_p95",
    sli=LATENCY_P95_SLI,
    target=5.0,  # p95 <= 5.0 seconds
    measurement_window="7d",
    max_acceptable_value=10.0,
)

ERROR_RATE_SLO = SLO(
    name="error_rate",
    sli=ERROR_RATE_SLI,
    target=0.01,  # <= 1% error rate
    measurement_window="7d",
    max_acceptable_value=0.05,
)

QUALITY_SLO = SLO(
    name="quality_score",
    sli=QUALITY_SCORE_SLI,
    target=0.95,  # >= 95% evaluation pass rate
    measurement_window="7d",
)

COST_SLO = SLO(
    name="cost_per_outcome",
    sli=COST_PER_OUTCOME_SLI,
    target=0.50,  # <= $0.50 USD per outcome
    measurement_window="7d",
    max_acceptable_value=1.00,
)

HALLUCINATION_SLO = SLO(
    name="hallucination_free_rate",
    sli=HALLUCINATION_FREE_SLI,
    target=0.98,  # >= 98% hallucination-free claims
    measurement_window="7d",
)

SAFETY_SLO = SLO(
    name="safety_compliance",
    sli=SAFETY_SLI,
    target=0.995,  # >= 99.5% policy-compliant executions
    measurement_window="30d",
)

DEFAULT_SLOS: dict[str, SLO] = {
    AVAILABILITY_SLO.name: AVAILABILITY_SLO,
    LATENCY_SLO.name: LATENCY_SLO,
    ERROR_RATE_SLO.name: ERROR_RATE_SLO,
    QUALITY_SLO.name: QUALITY_SLO,
    COST_SLO.name: COST_SLO,
    HALLUCINATION_SLO.name: HALLUCINATION_SLO,
    SAFETY_SLO.name: SAFETY_SLO,
}
