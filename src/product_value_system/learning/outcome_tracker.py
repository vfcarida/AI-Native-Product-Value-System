"""
Outcome Tracker linking deployed hypotheses to post-launch production measurements (EVO-010).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from scipy import stats

from product_value_system.learning.analytics_adapter import (
    AnalyticsAdapter,
    MockAnalyticsAdapter,
)
from product_value_system.learning.attribution import CostAttributor


@dataclass
class OutcomeMeasurement:
    """Measured outcome comparing production performance against hypothesis baseline/target."""

    outcome_id: str
    deployment_date: datetime
    measurement_start: datetime
    measurement_end: datetime

    primary_metric_name: str
    baseline_value: float
    target_value: float
    current_value: float

    status: str  # "ACHIEVED", "PARTIAL_SUCCESS", "NOT_ACHIEVED", "REGRESSED", "IN_PROGRESS"
    confidence_interval: tuple[float, float] | None
    p_value: float

    learning: list[str]
    recommendations: list[str]
    cost_attribution: dict[str, Any]
    guardrail_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize measurement to schema-compatible dictionary."""
        return {
            "outcome_id": self.outcome_id,
            "deployment_date": self.deployment_date.isoformat(),
            "measurement_start": self.measurement_start.isoformat(),
            "measurement_end": self.measurement_end.isoformat(),
            "primary_metric_name": self.primary_metric_name,
            "baseline_value": self.baseline_value,
            "target_value": self.target_value,
            "current_value": self.current_value,
            "status": self.status,
            "confidence_interval": (
                list(self.confidence_interval)
                if self.confidence_interval is not None
                else None
            ),
            "p_value": round(self.p_value, 6),
            "learning": list(self.learning),
            "recommendations": list(self.recommendations),
            "cost_attribution": dict(self.cost_attribution),
            "guardrail_results": list(self.guardrail_results),
        }


class OutcomeTracker:
    """
    Track deployed outcome hypotheses and evaluate actual production outcomes.

    Supports both canonical ``outcome.schema.json`` structure
    (``success_criteria.primary_metric`` + ``success_criteria.baseline/target``)
    and shorthand ``primary_metric`` dictionaries.
    """

    def __init__(
        self,
        analytics_adapter: AnalyticsAdapter,
        cost_attributor: CostAttributor | None = None,
    ) -> None:
        self.analytics = analytics_adapter
        self.cost_attributor = cost_attributor or CostAttributor()
        self.deployments: dict[str, dict[str, Any]] = {}
        self.measurements: dict[str, OutcomeMeasurement] = {}

    def register_deployment(
        self,
        outcome_id: str,
        deployment_date: datetime,
        outcome_hypothesis: dict[str, Any],
        *,
        development_cost_usd: float | None = None,
        stage_costs: dict[str, float] | None = None,
        ongoing_cost_per_month_usd: float | None = None,
    ) -> None:
        """
        Register a deployed outcome hypothesis for post-launch measurement.
        """
        self.deployments[outcome_id] = {
            "deployment_date": deployment_date,
            "hypothesis": outcome_hypothesis,
        }
        if development_cost_usd is not None:
            self.cost_attributor.record_development_cost(
                outcome_id,
                development_cost_usd,
                stage_costs=stage_costs,
                ongoing_cost_per_month_usd=ongoing_cost_per_month_usd,
            )

    def _extract_metric_spec(
        self, hypothesis: dict[str, Any]
    ) -> tuple[str, float, float, list[dict[str, Any]]]:
        """Extract (metric_name, baseline, target, guardrails) from canonical or shorthand dict."""
        if "success_criteria" in hypothesis and isinstance(hypothesis["success_criteria"], dict):
            sc = hypothesis["success_criteria"]
            pm = sc.get("primary_metric", {})
            metric_name = str(pm.get("name", "unknown_metric"))
            baseline = float(sc.get("baseline", pm.get("baseline", 0.0)))
            target = float(sc.get("target", pm.get("target", 0.0)))
            guardrails = list(sc.get("guardrails", []))
            return metric_name, baseline, target, guardrails

        pm = hypothesis.get("primary_metric", {})
        metric_name = str(pm.get("name", "unknown_metric"))
        baseline = float(pm.get("baseline", 0.0))
        target = float(pm.get("target", 0.0))
        guardrails = list(hypothesis.get("guardrails", []))
        return metric_name, baseline, target, guardrails

    async def measure_outcome(
        self,
        outcome_id: str,
        measurement_window_days: int = 30,
        baseline_window_days: int = 30,
    ) -> OutcomeMeasurement:
        """
        Query analytics for the post-deployment window, compare to baseline,
        check guardrails, attribute development cost, and generate learnings.
        """
        if outcome_id not in self.deployments:
            raise ValueError(f"Outcome '{outcome_id}' is not registered for measurement")

        deployment = self.deployments[outcome_id]
        deployment_date: datetime = deployment["deployment_date"]
        hypothesis: dict[str, Any] = deployment["hypothesis"]

        metric_name, declared_baseline, target_value, guardrails = self._extract_metric_spec(
            hypothesis
        )

        # Resolve baseline value: query pre-deployment window unless using single-value mock
        baseline_start = deployment_date - timedelta(days=baseline_window_days)
        baseline_end = deployment_date
        if (
            isinstance(self.analytics, MockAnalyticsAdapter)
            and self.analytics.baseline_data is None
        ):
            baseline_value = declared_baseline
        else:
            try:
                baseline_res = await self.analytics.query_metric(
                    metric_name=metric_name,
                    start_date=baseline_start,
                    end_date=baseline_end,
                    filters={"_window": "baseline"}
                    if isinstance(self.analytics, MockAnalyticsAdapter)
                    else None,
                )
                baseline_value = baseline_res.value
            except ValueError:
                baseline_value = declared_baseline

        # Query post-deployment current metric value
        measurement_start = deployment_date
        measurement_end = deployment_date + timedelta(days=measurement_window_days)
        current_result = await self.analytics.query_metric(
            metric_name=metric_name,
            start_date=measurement_start,
            end_date=measurement_end,
        )

        higher_is_better = target_value >= baseline_value
        status, p_value = self._determine_status(
            baseline=baseline_value,
            current=current_result.value,
            target=target_value,
            ci=current_result.confidence_interval,
            sample_size=current_result.sample_size,
            higher_is_better=higher_is_better,
        )

        # Evaluate guardrail metrics if present in analytics
        guardrail_results = await self._evaluate_guardrails(
            guardrails=guardrails,
            start_date=measurement_start,
            end_date=measurement_end,
        )

        learning = self._generate_learning(
            metric_name=metric_name,
            baseline=baseline_value,
            current=current_result.value,
            target=target_value,
            status=status,
            p_value=p_value,
            guardrail_results=guardrail_results,
        )
        recommendations = self._generate_recommendations(
            status=status,
            guardrail_results=guardrail_results,
        )
        attribution = self.cost_attributor.attribute(
            outcome_id=outcome_id,
            baseline_value=baseline_value,
            current_value=current_result.value,
            higher_is_better=higher_is_better,
        )

        measurement = OutcomeMeasurement(
            outcome_id=outcome_id,
            deployment_date=deployment_date,
            measurement_start=measurement_start,
            measurement_end=measurement_end,
            primary_metric_name=metric_name,
            baseline_value=baseline_value,
            target_value=target_value,
            current_value=current_result.value,
            status=status,
            confidence_interval=current_result.confidence_interval,
            p_value=p_value,
            learning=learning,
            recommendations=recommendations,
            cost_attribution=attribution.to_dict(),
            guardrail_results=guardrail_results,
        )
        self.measurements[outcome_id] = measurement
        return measurement

    async def _evaluate_guardrails(
        self,
        guardrails: list[dict[str, Any]],
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict[str, Any]]:
        """Evaluate any guardrail metrics available in the analytics backend."""
        results: list[dict[str, Any]] = []
        for g in guardrails:
            g_metric = str(g.get("metric", ""))
            if not g_metric:
                continue
            try:
                res = await self.analytics.query_metric(g_metric, start_date, end_date)
            except ValueError:
                continue

            threshold = g.get("threshold")
            direction = str(g.get("direction", "above")).lower()
            passed = True
            if isinstance(threshold, (int, float)):
                passed = (
                    res.value >= float(threshold)
                    if direction == "above"
                    else res.value <= float(threshold)
                )
            results.append(
                {
                    "metric": g_metric,
                    "value": res.value,
                    "threshold": threshold,
                    "direction": direction,
                    "passed": passed,
                }
            )
        return results

    def _determine_status(
        self,
        baseline: float,
        current: float,
        target: float,
        ci: tuple[float, float] | None,
        sample_size: int = 100,
        higher_is_better: bool = True,
    ) -> tuple[str, float]:
        """Compute statistical significance (two-sided t-test) and outcome status."""
        df = max(1, sample_size - 1)
        if ci is not None and ci[1] > ci[0]:
            se = (ci[1] - ci[0]) / (2.0 * 1.96)
        else:
            se = max(abs(baseline) * 0.01, 1e-6)

        if se <= 0.0:
            p_value = 0.0 if current != baseline else 1.0
        else:
            t_stat = (current - baseline) / se
            p_value = float(2.0 * (1.0 - stats.t.cdf(abs(t_stat), df)))

        if p_value > 0.05:
            return "NOT_ACHIEVED", p_value

        if higher_is_better:
            if current >= target:
                return "ACHIEVED", p_value
            if current > baseline:
                return "PARTIAL_SUCCESS", p_value
            return "REGRESSED", p_value

        if current <= target:
            return "ACHIEVED", p_value
        if current < baseline:
            return "PARTIAL_SUCCESS", p_value
        return "REGRESSED", p_value

    def _generate_learning(
        self,
        metric_name: str,
        baseline: float,
        current: float,
        target: float,
        status: str,
        p_value: float,
        guardrail_results: list[dict[str, Any]],
    ) -> list[str]:
        """Synthesize quantitative learnings from outcome measurement."""
        delta = current - baseline
        learnings = [
            f"Metric '{metric_name}' moved from {baseline:.2f} to {current:.2f} "
            f"(delta: {delta:+.2f}, p={p_value:.4f})"
        ]
        if baseline != 0:
            rel_pct = (delta / abs(baseline)) * 100.0
            learnings.append(f"Relative change from baseline: {rel_pct:+.1f}%")

        if status == "ACHIEVED":
            learnings.append(f"Target ({target:.2f}) achieved with statistical significance.")
        elif status == "PARTIAL_SUCCESS":
            learnings.append(
                f"Significant progress observed, but target ({target:.2f}) not yet met "
                f"(remaining gap: {abs(target - current):.2f})."
            )
        elif status == "REGRESSED":
            learnings.append(
                "Statistically significant regression detected relative to "
                f"baseline ({baseline:.2f})."
            )
        else:
            learnings.append("No statistically significant improvement observed over baseline.")

        for gr in guardrail_results:
            if not gr["passed"]:
                learnings.append(
                    f"Guardrail breached: '{gr['metric']}' measured {gr['value']:.2f} "
                    f"(expected {gr['direction']} {gr['threshold']})."
                )

        return learnings

    def _generate_recommendations(
        self,
        status: str,
        guardrail_results: list[dict[str, Any]],
    ) -> list[str]:
        """Produce actionable follow-up recommendations for the Discovery feedback loop."""
        recommendations: list[str] = []

        if any(not g["passed"] for g in guardrail_results):
            recommendations.append(
                "Halt rollout expansion and remediate breached guardrail metric(s) immediately."
            )

        if status == "ACHIEVED":
            recommendations.append("Document winning patterns and feed learnings into Discovery.")
            recommendations.append("Consider graduating rollout to 100% of eligible users.")
        elif status == "PARTIAL_SUCCESS":
            recommendations.append(
                "Investigate bottlenecks preventing full target attainment and plan iteration."
            )
            recommendations.append("Segment metric performance by user cohort and device type.")
        elif status == "REGRESSED":
            recommendations.append("Trigger rollback review and conduct root-cause postmortem.")
        else:
            recommendations.append("Revisit problem hypothesis and evidence in Discovery stage.")
            recommendations.append("Evaluate alternative interventions or extend sample window.")

        return recommendations
