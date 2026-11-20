"""
Automated production monitor and SLO-breach rollback controller (EVO-014).
"""

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from product_value_system.monitoring.error_budget import ErrorBudgetPolicy, ErrorBudgetStatus
from product_value_system.monitoring.metrics import PVSMetrics, get_metrics
from product_value_system.monitoring.slo import AVAILABILITY_SLO, QUALITY_SLO, SLO
from product_value_system.monitoring.slo_evaluator import AlertRuleEvaluation, SLOEvaluator

logger = logging.getLogger(__name__)


@dataclass
class RollbackEvent:
    """Audit record emitted whenever an automated rollback is triggered."""

    slo_name: str
    reason: str
    previous_version: str
    rolled_back_from_version: str
    triggered_by: str  # "error_budget_exhausted" | "critical_alert"
    budget_remaining: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Serialize rollback event to JSON/YAML dictionary."""
        return {
            "slo_name": self.slo_name,
            "reason": self.reason,
            "previous_version": self.previous_version,
            "rolled_back_from_version": self.rolled_back_from_version,
            "triggered_by": self.triggered_by,
            "budget_remaining": round(self.budget_remaining, 4),
            "timestamp": self.timestamp.isoformat(),
        }


class ProductionMonitor:
    """
    Continuously evaluates configured SLOs and triggers automated rollback (and
    optional runaway-agent kill switches) on error budget exhaustion or critical
    burn-rate alerts.
    """

    def __init__(
        self,
        *,
        slos: list[SLO] | None = None,
        metrics: PVSMetrics | None = None,
        rollback_enabled: bool = True,
        current_version: str = "0.2.0",
        previous_version: str = "0.1.0",
        rollback_callback: Callable[[RollbackEvent], Any] | None = None,
        kill_switch_callback: Callable[[], Any] | None = None,
    ) -> None:
        self.metrics = metrics or get_metrics()
        self.evaluator = SLOEvaluator(self.metrics)
        self.slos = list(slos) if slos is not None else [AVAILABILITY_SLO, QUALITY_SLO]
        self.policies = {slo.name: ErrorBudgetPolicy(slo=slo) for slo in self.slos}
        self.rollback_enabled = rollback_enabled
        self.current_version = current_version
        self.previous_version = previous_version
        self.rollback_callback = rollback_callback
        self.kill_switch_callback = kill_switch_callback

        self.rollback_triggered: bool = False
        self.rollback_reason: str | None = None
        self.rollback_events: list[RollbackEvent] = []

    async def evaluate_slos(
        self,
        *,
        alerts: list[AlertRuleEvaluation] | None = None,
    ) -> dict[str, ErrorBudgetStatus]:
        """
        Evaluate all monitored SLOs and trigger automated rollback if any error
        budget is exhausted or a ``critical`` alert is firing.
        """
        statuses: dict[str, ErrorBudgetStatus] = {}
        for slo in self.slos:
            status = await self.evaluator.evaluate_slo(slo, self.policies.get(slo.name))
            statuses[slo.name] = status

            if (
                self.rollback_enabled
                and not self.rollback_triggered
                and status.budget_remaining <= 0.0
            ):
                await self._trigger_rollback(
                    slo_name=slo.name,
                    reason=f"Error budget exhausted for {slo.name} SLO",
                    triggered_by="error_budget_exhausted",
                    budget_remaining=status.budget_remaining,
                )

        if alerts and self.rollback_enabled and not self.rollback_triggered:
            for alert in alerts:
                if alert.firing and alert.severity.lower() == "critical":
                    await self._trigger_rollback(
                        slo_name=alert.alert_name,
                        reason=f"Critical alert firing: {alert.summary}",
                        triggered_by="critical_alert",
                        budget_remaining=0.0,
                    )
                    break

        return statuses

    async def _trigger_rollback(
        self,
        *,
        slo_name: str,
        reason: str,
        triggered_by: str,
        budget_remaining: float,
    ) -> RollbackEvent:
        """Revert active version to ``previous_version``, invoke hooks, and log event."""
        event = RollbackEvent(
            slo_name=slo_name,
            reason=reason,
            previous_version=self.previous_version,
            rolled_back_from_version=self.current_version,
            triggered_by=triggered_by,
            budget_remaining=budget_remaining,
        )
        self.rollback_triggered = True
        self.rollback_reason = reason
        self.current_version = self.previous_version
        self.rollback_events.append(event)

        logger.critical(
            "AUTOMATED ROLLBACK triggered (%s): reverting %s -> %s (%s)",
            triggered_by,
            event.rolled_back_from_version,
            event.previous_version,
            reason,
        )

        if self.kill_switch_callback is not None:
            ks_res = self.kill_switch_callback()
            if inspect.isawaitable(ks_res):
                await ks_res

        if self.rollback_callback is not None:
            cb_res = self.rollback_callback(event)
            if inspect.isawaitable(cb_res):
                await cb_res

        return event
