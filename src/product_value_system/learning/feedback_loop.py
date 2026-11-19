"""
Continuous feedback loop connecting production outcome measurements back to Discovery (EVO-010).

Provides:
- Automated alerting for metric regressions, guardrail breaches, and missed targets
- OpenTelemetry span events (``learning.alert``) for observability integration
- Persistent feedback store and structured Discovery context generation
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from opentelemetry import trace

from product_value_system.learning.outcome_tracker import OutcomeMeasurement, OutcomeTracker


@dataclass
class FeedbackAlert:
    """Alert generated when a measured outcome regresses, breaches guardrails, or misses target."""

    outcome_id: str
    severity: str  # "critical", "warning", "info"
    alert_type: str  # "regression", "guardrail_breach", "target_missed", "target_achieved"
    message: str
    metric_name: str
    baseline_value: float
    current_value: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Serialize alert to JSON/YAML dictionary."""
        return {
            "outcome_id": self.outcome_id,
            "severity": self.severity,
            "alert_type": self.alert_type,
            "message": self.message,
            "metric_name": self.metric_name,
            "baseline_value": self.baseline_value,
            "current_value": self.current_value,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class DiscoveryFeedbackItem:
    """Structured learning item fed back into future Discovery stage runs."""

    outcome_id: str
    metric_name: str
    status: str
    summary: str
    learnings: list[str]
    recommendations: list[str]
    cost_per_unit_improvement: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Serialize discovery feedback item to dictionary."""
        return {
            "outcome_id": self.outcome_id,
            "metric_name": self.metric_name,
            "status": self.status,
            "summary": self.summary,
            "learnings": list(self.learnings),
            "recommendations": list(self.recommendations),
            "cost_per_unit_improvement": self.cost_per_unit_improvement,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiscoveryFeedbackItem":
        """Reconstruct item from persisted YAML/JSON dictionary."""
        raw_ts = data.get("created_at")
        created_at = (
            datetime.fromisoformat(raw_ts) if isinstance(raw_ts, str) else datetime.now(UTC)
        )
        return cls(
            outcome_id=str(data["outcome_id"]),
            metric_name=str(data["metric_name"]),
            status=str(data["status"]),
            summary=str(data["summary"]),
            learnings=list(data.get("learnings", [])),
            recommendations=list(data.get("recommendations", [])),
            cost_per_unit_improvement=data.get("cost_per_unit_improvement"),
            created_at=created_at,
        )


class FeedbackLoop:
    """
    Continuous learning loop evaluating deployed outcomes, triggering alerts,
    and synthesizing historical feedback for subsequent Discovery cycles.
    """

    def __init__(
        self,
        outcome_tracker: OutcomeTracker,
        *,
        alert_callback: Callable[[FeedbackAlert], Any] | None = None,
        feedback_store_path: Path | str | None = None,
        include_info_alerts: bool = False,
    ) -> None:
        self.outcome_tracker = outcome_tracker
        self.alert_callback = alert_callback
        self.feedback_store_path = (
            Path(feedback_store_path) if feedback_store_path is not None else None
        )
        self.include_info_alerts = include_info_alerts
        self.alerts: list[FeedbackAlert] = []
        self.feedback_items: list[DiscoveryFeedbackItem] = []

        if self.feedback_store_path is not None and self.feedback_store_path.exists():
            self.load_feedback_history()

    async def evaluate_measurement(
        self,
        measurement: OutcomeMeasurement,
    ) -> list[FeedbackAlert]:
        """
        Inspect an outcome measurement, generate alerts for regressions or guardrail
        breaches, emit OpenTelemetry span events, and notify any configured callback.
        """
        new_alerts: list[FeedbackAlert] = []

        if measurement.status == "REGRESSED":
            new_alerts.append(
                FeedbackAlert(
                    outcome_id=measurement.outcome_id,
                    severity="critical",
                    alert_type="regression",
                    message=(
                        f"Outcome '{measurement.outcome_id}' regressed on "
                        f"'{measurement.primary_metric_name}': "
                        f"{measurement.baseline_value:.2f} -> {measurement.current_value:.2f} "
                        f"(p={measurement.p_value:.4f})"
                    ),
                    metric_name=measurement.primary_metric_name,
                    baseline_value=measurement.baseline_value,
                    current_value=measurement.current_value,
                )
            )
        elif measurement.status == "NOT_ACHIEVED":
            new_alerts.append(
                FeedbackAlert(
                    outcome_id=measurement.outcome_id,
                    severity="warning",
                    alert_type="target_missed",
                    message=(
                        f"Outcome '{measurement.outcome_id}' showed no significant improvement on "
                        f"'{measurement.primary_metric_name}' "
                        f"(current={measurement.current_value:.2f}, "
                        f"target={measurement.target_value:.2f})"
                    ),
                    metric_name=measurement.primary_metric_name,
                    baseline_value=measurement.baseline_value,
                    current_value=measurement.current_value,
                )
            )
        elif measurement.status == "ACHIEVED" and self.include_info_alerts:
            new_alerts.append(
                FeedbackAlert(
                    outcome_id=measurement.outcome_id,
                    severity="info",
                    alert_type="target_achieved",
                    message=(
                        f"Outcome '{measurement.outcome_id}' achieved target on "
                        f"'{measurement.primary_metric_name}' ({measurement.current_value:.2f})"
                    ),
                    metric_name=measurement.primary_metric_name,
                    baseline_value=measurement.baseline_value,
                    current_value=measurement.current_value,
                )
            )

        for guardrail in measurement.guardrail_results:
            if not guardrail.get("passed", True):
                new_alerts.append(
                    FeedbackAlert(
                        outcome_id=measurement.outcome_id,
                        severity="critical",
                        alert_type="guardrail_breach",
                        message=(
                            f"Guardrail '{guardrail['metric']}' breached for "
                            f"'{measurement.outcome_id}': measured {guardrail['value']:.2f}, "
                            f"expected {guardrail['direction']} {guardrail['threshold']}"
                        ),
                        metric_name=str(guardrail["metric"]),
                        baseline_value=float(guardrail.get("threshold") or 0.0),
                        current_value=float(guardrail["value"]),
                    )
                )

        span = trace.get_current_span()
        for alert in new_alerts:
            self.alerts.append(alert)
            if span is not None and span.is_recording():
                span.add_event(
                    "learning.alert",
                    attributes={
                        "pvs.outcome_id": alert.outcome_id,
                        "pvs.alert.severity": alert.severity,
                        "pvs.alert.type": alert.alert_type,
                        "pvs.alert.metric": alert.metric_name,
                        "pvs.alert.message": alert.message,
                    },
                )
            if self.alert_callback is not None:
                res = self.alert_callback(alert)
                if inspect.isawaitable(res):
                    await res

        return new_alerts

    def record_feedback(self, measurement: OutcomeMeasurement) -> DiscoveryFeedbackItem:
        """Convert a measurement into a Discovery feedback record and persist if configured."""
        summary = (
            f"Outcome '{measurement.outcome_id}' ({measurement.primary_metric_name}): "
            f"{measurement.status} — {measurement.baseline_value:.2f} -> "
            f"{measurement.current_value:.2f} (target {measurement.target_value:.2f})"
        )
        item = DiscoveryFeedbackItem(
            outcome_id=measurement.outcome_id,
            metric_name=measurement.primary_metric_name,
            status=measurement.status,
            summary=summary,
            learnings=list(measurement.learning),
            recommendations=list(measurement.recommendations),
            cost_per_unit_improvement=measurement.cost_attribution.get(
                "cost_per_unit_improvement"
            ),
        )
        self.feedback_items.append(item)
        self._persist_feedback()
        return item

    async def run_measurement_cycle(
        self,
        outcome_ids: list[str] | None = None,
        measurement_window_days: int = 30,
        baseline_window_days: int = 30,
    ) -> dict[str, OutcomeMeasurement]:
        """
        Execute a continuous measurement sweep across registered deployments,
        generating alerts and Discovery feedback records for each measured outcome.
        """
        target_ids = (
            list(outcome_ids)
            if outcome_ids is not None
            else list(self.outcome_tracker.deployments.keys())
        )
        results: dict[str, OutcomeMeasurement] = {}
        for outcome_id in target_ids:
            measurement = await self.outcome_tracker.measure_outcome(
                outcome_id=outcome_id,
                measurement_window_days=measurement_window_days,
                baseline_window_days=baseline_window_days,
            )
            await self.evaluate_measurement(measurement)
            self.record_feedback(measurement)
            results[outcome_id] = measurement
        return results

    def build_discovery_context(self, limit: int = 5) -> dict[str, Any]:
        """
        Build structured historical learning context suitable for injection into
        subsequent DiscoveryAgent runs.
        """
        recent = self.feedback_items[-limit:] if limit > 0 else []
        winning_patterns = [
            item.summary for item in recent if item.status in ("ACHIEVED", "PARTIAL_SUCCESS")
        ]
        anti_patterns = [
            item.summary for item in recent if item.status in ("REGRESSED", "NOT_ACHIEVED")
        ]
        recommendations: list[str] = []
        for item in recent:
            for rec in item.recommendations:
                if rec not in recommendations:
                    recommendations.append(rec)

        return {
            "total_measured_outcomes": len(self.feedback_items),
            "historical_outcomes": [item.to_dict() for item in recent],
            "winning_patterns": winning_patterns,
            "anti_patterns": anti_patterns,
            "open_recommendations": recommendations,
        }

    def _persist_feedback(self) -> None:
        """Persist feedback records to YAML when ``feedback_store_path`` is set."""
        if self.feedback_store_path is None:
            return
        self.feedback_store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"feedback_items": [item.to_dict() for item in self.feedback_items]}
        with open(self.feedback_store_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, sort_keys=False)

    def load_feedback_history(self) -> list[DiscoveryFeedbackItem]:
        """Reload persisted feedback items from ``feedback_store_path``."""
        if self.feedback_store_path is None or not self.feedback_store_path.exists():
            return []
        with open(self.feedback_store_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        items = [
            DiscoveryFeedbackItem.from_dict(entry)
            for entry in raw.get("feedback_items", [])
            if isinstance(entry, dict)
        ]
        self.feedback_items = items
        return list(self.feedback_items)
