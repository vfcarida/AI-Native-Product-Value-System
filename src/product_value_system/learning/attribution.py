"""
Cost-to-outcome attribution engine (REQ-073, EVO-010).

Links LLM/agent development costs and ongoing infrastructure costs directly
to achieved product outcome improvements.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CostAttributionReport:
    """Financial attribution of development and operational costs to an outcome."""

    outcome_id: str
    development_cost_usd: float
    ongoing_cost_per_month_usd: float
    improvement: float
    relative_improvement_pct: float
    cost_per_unit_improvement: float | None
    stage_costs: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize cost attribution report to JSON/YAML-friendly dictionary."""
        return {
            "outcome_id": self.outcome_id,
            "development_cost_usd": round(self.development_cost_usd, 4),
            "ongoing_cost_per_month_usd": round(self.ongoing_cost_per_month_usd, 4),
            "improvement": round(self.improvement, 4),
            "relative_improvement_pct": round(self.relative_improvement_pct, 2),
            "cost_per_unit_improvement": (
                round(self.cost_per_unit_improvement, 4)
                if self.cost_per_unit_improvement is not None
                else None
            ),
            "stage_costs": dict(self.stage_costs),
        }


class CostAttributor:
    """
    Records value-stream development costs per outcome and computes
    cost-per-improvement attribution once production outcomes are measured.
    """

    def __init__(self, default_ongoing_monthly_usd: float = 0.0) -> None:
        self.default_ongoing_monthly_usd = default_ongoing_monthly_usd
        self._development_costs: dict[str, float] = {}
        self._ongoing_costs: dict[str, float] = {}
        self._stage_breakdowns: dict[str, dict[str, float]] = {}

    def record_development_cost(
        self,
        outcome_id: str,
        cost_usd: float,
        *,
        stage_costs: dict[str, float] | None = None,
        ongoing_cost_per_month_usd: float | None = None,
    ) -> None:
        """Record LLM/agent development costs associated with an outcome."""
        self._development_costs[outcome_id] = (
            self._development_costs.get(outcome_id, 0.0) + float(cost_usd)
        )
        if stage_costs:
            existing = self._stage_breakdowns.setdefault(outcome_id, {})
            for stage, val in stage_costs.items():
                existing[stage] = existing.get(stage, 0.0) + float(val)
        if ongoing_cost_per_month_usd is not None:
            self._ongoing_costs[outcome_id] = float(ongoing_cost_per_month_usd)

    def attribute(
        self,
        outcome_id: str,
        baseline_value: float,
        current_value: float,
        *,
        higher_is_better: bool = True,
    ) -> CostAttributionReport:
        """
        Attribute development and ongoing costs to the measured metric improvement.
        """
        dev_cost = self._development_costs.get(outcome_id, 0.0)
        ongoing_cost = self._ongoing_costs.get(
            outcome_id, self.default_ongoing_monthly_usd
        )
        stage_costs = dict(self._stage_breakdowns.get(outcome_id, {}))

        raw_delta = current_value - baseline_value
        improvement = raw_delta if higher_is_better else -raw_delta
        relative_pct = (
            (improvement / abs(baseline_value)) * 100.0 if baseline_value != 0 else 0.0
        )
        cost_per_unit = (dev_cost / improvement) if improvement > 0 else None

        return CostAttributionReport(
            outcome_id=outcome_id,
            development_cost_usd=dev_cost,
            ongoing_cost_per_month_usd=ongoing_cost,
            improvement=improvement,
            relative_improvement_pct=relative_pct,
            cost_per_unit_improvement=cost_per_unit,
            stage_costs=stage_costs,
        )
