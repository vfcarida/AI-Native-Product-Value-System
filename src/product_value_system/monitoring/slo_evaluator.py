"""
SLO evaluator and multi-window, multi-burn-rate alert engine (EVO-014).
"""

from dataclasses import dataclass
from typing import Any

from product_value_system.monitoring.error_budget import ErrorBudgetPolicy, ErrorBudgetStatus
from product_value_system.monitoring.metrics import PVSMetrics, get_metrics
from product_value_system.monitoring.sli import SLIType
from product_value_system.monitoring.slo import DEFAULT_SLOS, SLO


@dataclass
class AlertRuleEvaluation:
    """Evaluation outcome for a single multi-window burn-rate or threshold alert."""

    alert_name: str
    severity: str  # "critical", "warning"
    firing: bool
    burn_rate: float
    value: float
    threshold: float
    summary: str
    runbook_url: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize alert evaluation to dictionary."""
        return {
            "alert_name": self.alert_name,
            "severity": self.severity,
            "firing": self.firing,
            "burn_rate": round(self.burn_rate, 4),
            "value": round(self.value, 6),
            "threshold": round(self.threshold, 6),
            "summary": self.summary,
            "runbook_url": self.runbook_url,
        }


class SLOEvaluator:
    """
    Computes live SLI compliance from ``PVSMetrics``, updates remaining error
    budgets, and evaluates Google SRE multi-window, multi-burn-rate alerts.
    """

    def __init__(self, metrics: PVSMetrics | None = None) -> None:
        self.metrics = metrics or get_metrics()

    async def compute_compliance(self, slo: SLO) -> float:
        """Compute current observed SLI value for ``slo`` from ``PVSMetrics``."""
        snap = self.metrics.get_snapshot()
        sli_type = slo.sli.type

        if sli_type == SLIType.AVAILABILITY:
            return float(snap["availability"])
        if sli_type == SLIType.ERROR_RATE:
            return float(snap["error_rate"])
        if sli_type == SLIType.LATENCY:
            return float(snap["latency_p95_seconds"])
        if sli_type == SLIType.QUALITY_SCORE:
            return float(snap["eval_pass_rate"])
        if sli_type == SLIType.COST:
            return float(snap["avg_cost_per_outcome_usd"])
        if sli_type == SLIType.HALLUCINATION:
            return float(snap["hallucination_free_rate"])
        if sli_type == SLIType.SAFETY:
            return float(snap["safety_compliance"])
        if sli_type == SLIType.SATURATION:
            return float(snap["active_runs"])
        return float(snap["requests_success"])

    async def evaluate_slo(
        self,
        slo: SLO,
        policy: ErrorBudgetPolicy | None = None,
    ) -> ErrorBudgetStatus:
        """Evaluate ``slo`` against live metrics and persist remaining budget gauge."""
        actual = await self.compute_compliance(slo)
        active_policy = policy or ErrorBudgetPolicy(slo=slo)
        status = active_policy.evaluate(actual)
        self.metrics.set_error_budget(slo.name, status.budget_remaining)
        return status

    async def evaluate_all(
        self,
        slos: dict[str, SLO] | None = None,
    ) -> dict[str, ErrorBudgetStatus]:
        """Evaluate all configured SLOs and return status map keyed by SLO name."""
        catalog = slos if slos is not None else DEFAULT_SLOS
        results: dict[str, ErrorBudgetStatus] = {}
        for name, slo in catalog.items():
            results[name] = await self.evaluate_slo(slo)
        return results

    def evaluate_burn_rate_alerts(
        self,
        slo: SLO,
        *,
        short_window_error_rate: float,
        long_window_error_rate: float,
        fast_burn_multiplier: float = 10.0,
        slow_burn_multiplier: float = 2.0,
    ) -> list[AlertRuleEvaluation]:
        """
        Evaluate Google SRE multi-window, multi-burn-rate alert rules:

        - ``SLOBurnRateFast`` (critical): both 5m and 1h error rates exceed ``10x`` error budget
        - ``SLOBurnRateSlow`` (warning): both 30m and 6h error rates exceed ``2x`` error budget
        """
        budget = max(slo.compute_error_budget(), 1e-9)
        fast_threshold = fast_burn_multiplier * budget
        slow_threshold = slow_burn_multiplier * budget

        fast_firing = (
            short_window_error_rate > fast_threshold and long_window_error_rate > fast_threshold
        )
        slow_firing = (
            short_window_error_rate > slow_threshold and long_window_error_rate > slow_threshold
        )

        burn_rate = min(short_window_error_rate, long_window_error_rate) / budget

        return [
            AlertRuleEvaluation(
                alert_name="SLOBurnRateFast",
                severity="critical",
                firing=fast_firing,
                burn_rate=burn_rate,
                value=short_window_error_rate,
                threshold=fast_threshold,
                summary=(
                    f"Fast SLO burn rate on '{slo.name}' ({burn_rate:.1f}x budget; "
                    f"threshold >{fast_burn_multiplier:.0f}x)"
                ),
                runbook_url="docs/runbooks/alerting.md#sloburnratefast",
            ),
            AlertRuleEvaluation(
                alert_name="SLOBurnRateSlow",
                severity="warning",
                firing=slow_firing,
                burn_rate=burn_rate,
                value=long_window_error_rate,
                threshold=slow_threshold,
                summary=(
                    f"Slow SLO burn rate on '{slo.name}' ({burn_rate:.1f}x budget; "
                    f"threshold >{slow_burn_multiplier:.0f}x)"
                ),
                runbook_url="docs/runbooks/alerting.md#sloburnrateslow",
            ),
        ]
