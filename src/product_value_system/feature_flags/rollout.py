"""
Progressive rollout controller with stage quality gates and automatic rollback (EVO-015).
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from product_value_system.feature_flags.models import (
    DEFAULT_STAGE_QUALITY_GATES,
    STAGE_ORDER,
    STAGE_PERCENTAGES,
    FeatureFlag,
    RolloutStage,
)
from product_value_system.feature_flags.service import FeatureFlagService
from product_value_system.monitoring.metrics import PVSMetrics, get_metrics

logger = logging.getLogger(__name__)


@runtime_checkable
class SLOMonitorProtocol(Protocol):
    """Protocol for SLO monitoring backends queried by ``RolloutController``."""

    async def get_error_rate(self, flag_name: str) -> float:
        """Return observed error rate in [0.0, 1.0] for ``flag_name``."""

    async def get_quality_score(self, flag_name: str) -> float:
        """Return observed quality score in [0.0, 1.0] for ``flag_name``."""


class MetricsSLOMonitor:
    """
    Production SLO monitor bridging EVO-014 ``PVSMetrics`` into ``RolloutController``.
    """

    def __init__(
        self,
        metrics: PVSMetrics | None = None,
        *,
        crash_rate: float = 0.0,
        critical_alerts: list[str] | None = None,
    ) -> None:
        self.metrics = metrics or get_metrics()
        self._crash_rate = crash_rate
        self._critical_alerts: list[str] = list(critical_alerts or [])

    async def get_error_rate(self, flag_name: str) -> float:  # noqa: ARG002
        snap = self.metrics.get_snapshot()
        return float(snap["error_rate"])

    async def get_quality_score(self, flag_name: str) -> float:  # noqa: ARG002
        snap = self.metrics.get_snapshot()
        return float(snap["eval_pass_rate"])

    async def get_crash_rate(self, flag_name: str) -> float:  # noqa: ARG002
        return float(self._crash_rate)

    async def has_critical_alert(self, flag_name: str) -> str | None:
        """Return description of active CRITICAL alert affecting ``flag_name`` if any."""
        if not self._critical_alerts:
            return None
        for alert in self._critical_alerts:
            if flag_name in alert or alert.startswith("CRITICAL:"):
                return alert
        return None

    def trigger_critical_alert(self, alert_summary: str) -> None:
        """Register an active CRITICAL alert (e.g., from Alertmanager webhook)."""
        self._critical_alerts.append(alert_summary)

    def clear_critical_alerts(self) -> None:
        """Clear active critical alerts."""
        self._critical_alerts.clear()


@dataclass
class RolloutAdvanceResult:
    """Structured outcome returned by ``RolloutController.advance_rollout``."""

    flag_name: str
    advanced: bool
    from_stage: RolloutStage
    to_stage: RolloutStage
    rollout_percentage: float
    rolled_back: bool = False
    reason: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Serialize rollout advance outcome to dictionary."""
        return {
            "flag_name": self.flag_name,
            "advanced": self.advanced,
            "from_stage": self.from_stage.value,
            "to_stage": self.to_stage.value,
            "rollout_percentage": self.rollout_percentage,
            "rolled_back": self.rolled_back,
            "reason": self.reason,
            "timestamp": self.timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }


class RolloutController:
    """
    Manages progressive delivery stage transitions, verifies per-stage SLO quality
    gates, and triggers automatic flag rollback on SLO breach or critical alerts.
    """

    def __init__(
        self,
        flag_service: FeatureFlagService,
        slo_monitor: Any,
        *,
        auto_rollback_enabled: bool = True,
        quality_gates: dict[RolloutStage, dict[str, float]] | None = None,
        alert_callback: Callable[[str], Any] | None = None,
        event_callback: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.flag_service = flag_service
        self.slo_monitor = slo_monitor
        self.auto_rollback_enabled = auto_rollback_enabled
        self.quality_gates: dict[RolloutStage, dict[str, float]] = (
            dict(quality_gates) if quality_gates is not None else dict(DEFAULT_STAGE_QUALITY_GATES)
        )
        self.alert_callback = alert_callback
        self.event_callback = event_callback
        self.events: list[dict[str, Any]] = []
        self.alerts_sent: list[str] = []

    async def advance_rollout(
        self,
        flag_name: str,
        *,
        actor: str = "rollout-controller",
    ) -> RolloutAdvanceResult:
        """
        Advance ``flag_name`` to the next progressive delivery stage if quality gates pass.

        Stage order: ``dark -> canary -> staged_5 -> staged_25 -> staged_50 -> full``.
        """
        flag = self.flag_service.flags.get(flag_name)
        if flag is None:
            raise KeyError(f"Flag not found: {flag_name}")

        current_stage = flag.rollout_stage

        if not await self._check_quality_gates(flag):
            logger.warning("Quality gates failed for %s at %s", flag_name, current_stage.value)
            return RolloutAdvanceResult(
                flag_name=flag_name,
                advanced=False,
                from_stage=current_stage,
                to_stage=flag.rollout_stage,
                rollout_percentage=flag.rollout_percentage,
                rolled_back=not flag.enabled,
                reason=f"Quality gates failed at stage '{current_stage.value}'",
            )

        next_stage = self._get_next_stage(current_stage)
        if next_stage is None:
            logger.info("Flag %s already at full rollout", flag_name)
            return RolloutAdvanceResult(
                flag_name=flag_name,
                advanced=False,
                from_stage=current_stage,
                to_stage=current_stage,
                rollout_percentage=flag.rollout_percentage,
                reason="Flag already at full rollout",
            )

        logger.info("Advancing %s from %s to %s", flag_name, current_stage.value, next_stage.value)

        flag.rollout_stage = next_stage
        flag.rollout_percentage = self._get_stage_percentage(next_stage)
        flag.updated_at = datetime.now(UTC)

        await self._save_flag(flag)
        self.flag_service.record_audit(
            action="advance",
            flag_name=flag_name,
            actor=actor,
            details={
                "from_stage": current_stage.value,
                "to_stage": next_stage.value,
                "percentage": flag.rollout_percentage,
            },
        )

        await self._emit_event(
            "rollout_advanced",
            {
                "flag_name": flag_name,
                "from_stage": current_stage.value,
                "to_stage": next_stage.value,
                "percentage": flag.rollout_percentage,
            },
        )

        return RolloutAdvanceResult(
            flag_name=flag_name,
            advanced=True,
            from_stage=current_stage,
            to_stage=next_stage,
            rollout_percentage=flag.rollout_percentage,
            reason=f"Advanced from {current_stage.value} to {next_stage.value}",
        )

    async def rollback(
        self,
        flag_name: str,
        reason: str,
        *,
        actor: str = "rollout-controller",
    ) -> dict[str, Any]:
        """Disable ``flag_name`` immediately, persist config, emit event, and alert team."""
        flag = self.flag_service.flags.get(flag_name)
        if flag is None:
            raise KeyError(f"Flag not found: {flag_name}")

        current_stage = flag.rollout_stage
        logger.error("Rolling back %s from %s. Reason: %s", flag_name, current_stage.value, reason)

        flag.enabled = False
        flag.updated_at = datetime.now(UTC)

        await self._save_flag(flag)
        self.flag_service.record_audit(
            action="rollback",
            flag_name=flag_name,
            actor=actor,
            details={"stage": current_stage.value, "reason": reason},
        )

        payload = {
            "flag_name": flag_name,
            "stage": current_stage.value,
            "reason": reason,
            "enabled": False,
        }
        await self._emit_event("rollback_triggered", payload)
        await self._alert_team(f"Rollback triggered for {flag_name}: {reason}")
        return payload

    async def _check_quality_gates(self, flag: FeatureFlag) -> bool:
        """
        Verify stage quality gates and critical alerts against ``self.slo_monitor``.

        Default quality gates:
        - Dark launch: ``error_rate <= 0.0``, ``crash_rate <= 0.0``
        - Canary / Staged / Full: ``error_rate <= 0.01``, ``quality_score >= 0.95``
        """
        gates = self.quality_gates.get(
            flag.rollout_stage,
            DEFAULT_STAGE_QUALITY_GATES[RolloutStage.CANARY],
        )

        # Check active CRITICAL alerts first
        critical_alert = await self._read_critical_alert(flag.name)
        if critical_alert:
            reason = f"CRITICAL alert active: {critical_alert}"
            logger.warning("%s (flag=%s)", reason, flag.name)
            if self.auto_rollback_enabled:
                await self.rollback(flag.name, reason)
            return False

        # Check error rate gate
        error_rate = await self._read_metric("get_error_rate", "error_rate", flag.name, default=0.0)
        max_error_rate = gates.get("error_rate", 1.0)
        if error_rate > max_error_rate:
            reason = f"Error rate {error_rate:.2%} > {max_error_rate:.2%}"
            logger.warning("Gate failure for %s: %s", flag.name, reason)
            if self.auto_rollback_enabled:
                await self.rollback(flag.name, reason)
            return False

        # Check crash rate gate (dark launch)
        if "crash_rate" in gates:
            crash_rate = await self._read_metric(
                "get_crash_rate",
                "crash_rate",
                flag.name,
                default=0.0,
            )
            max_crash_rate = gates["crash_rate"]
            if crash_rate > max_crash_rate:
                reason = f"Crash rate {crash_rate:.2%} > {max_crash_rate:.2%}"
                logger.warning("Gate failure for %s: %s", flag.name, reason)
                if self.auto_rollback_enabled:
                    await self.rollback(flag.name, reason)
                return False

        # Check quality score gate (canary, staged, full)
        if "quality_score" in gates:
            quality_score = await self._read_metric(
                "get_quality_score",
                "quality_score",
                flag.name,
                default=1.0,
            )
            min_quality = gates["quality_score"]
            if quality_score < min_quality:
                reason = f"Quality score {quality_score:.2%} < {min_quality:.2%}"
                logger.warning("Gate failure for %s: %s", flag.name, reason)
                if self.auto_rollback_enabled:
                    await self.rollback(flag.name, reason)
                return False

        return True

    async def _read_metric(
        self,
        method_name: str,
        attr_name: str,
        flag_name: str,
        *,
        default: float,
    ) -> float:
        method = getattr(self.slo_monitor, method_name, None)
        if callable(method):
            res = method(flag_name=flag_name)
            if inspect.isawaitable(res):
                res = await res
            return float(res)
        if hasattr(self.slo_monitor, attr_name):
            return float(getattr(self.slo_monitor, attr_name))
        return default

    async def _read_critical_alert(self, flag_name: str) -> str | None:
        method = getattr(self.slo_monitor, "has_critical_alert", None)
        if callable(method):
            res = method(flag_name=flag_name)
            if inspect.isawaitable(res):
                res = await res
            return str(res) if res else None
        return None

    async def _save_flag(self, flag: FeatureFlag) -> None:  # noqa: ARG002
        self.flag_service.save()

    async def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        record = {
            "event_type": event_type,
            "data": data,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        self.events.append(record)
        if self.event_callback is not None:
            res = self.event_callback(event_type, data)
            if inspect.isawaitable(res):
                await res

    async def _alert_team(self, message: str) -> None:
        self.alerts_sent.append(message)
        logger.warning("TEAM ALERT: %s", message)
        if self.alert_callback is not None:
            res = self.alert_callback(message)
            if inspect.isawaitable(res):
                await res

    def _get_next_stage(self, current: RolloutStage) -> RolloutStage | None:
        """Return next progressive delivery stage after ``current`` (or None at FULL)."""
        try:
            idx = STAGE_ORDER.index(current)
            if idx < len(STAGE_ORDER) - 1:
                return STAGE_ORDER[idx + 1]
        except ValueError:
            return None
        return None

    def _get_stage_percentage(self, stage: RolloutStage) -> float:
        """Map ``RolloutStage`` to its target traffic percentage."""
        return STAGE_PERCENTAGES[stage]
