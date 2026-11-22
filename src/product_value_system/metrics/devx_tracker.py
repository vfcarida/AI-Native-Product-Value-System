"""
Developer Experience (DevX) metrics tracker emitting Prometheus counters, histograms,
and gauges for SPACE framework & DORA metrics with privacy safeguards (EVO-032).
"""

from __future__ import annotations

import hashlib
import logging
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram

from product_value_system.metrics.cycle_time_tracker import CycleTimeTracker, DORAMetrics

logger = logging.getLogger(__name__)

_CYCLE_TIME_BUCKETS = (60.0, 300.0, 900.0, 1800.0, 3600.0, 7200.0, 14400.0, 28800.0)
_PR_REVIEW_BUCKETS = (300.0, 900.0, 1800.0, 3600.0, 14400.0, 43200.0, 86400.0)

# Default process-wide Prometheus metrics (scraped via /metrics)
devx_tasks_started = Counter(
    "devx_tasks_started_total",
    "Total developer / agent tasks started",
    ["stage", "agent"],
)
devx_tasks_completed = Counter(
    "devx_tasks_completed_total",
    "Total developer / agent tasks completed",
    ["stage", "agent", "success"],
)
devx_cycle_time_seconds = Histogram(
    "devx_cycle_time_seconds",
    "Task cycle time in seconds",
    ["stage"],
    buckets=_CYCLE_TIME_BUCKETS,
)
devx_ai_usage = Counter(
    "devx_ai_usage_total",
    "AI assist invocations by agent and acceptance verdict",
    ["agent", "accepted"],
)
devx_satisfaction_score = Gauge(
    "devx_satisfaction_score",
    "Aggregated developer satisfaction survey score (1-5)",
    ["dimension"],
)
devx_space_score = Gauge(
    "devx_space_score",
    "Computed SPACE framework dimension score (0.0-1.0)",
    ["dimension"],
)
devx_pr_review_turnaround_seconds = Histogram(
    "devx_pr_review_turnaround_seconds",
    "PR review turnaround time in seconds",
    buckets=_PR_REVIEW_BUCKETS,
)
devx_deployments_total = Counter(
    "devx_deployments_total",
    "Deployments tracked for DORA deployment frequency and change failure rate",
    ["success"],
)


def load_devx_config(path: Path | str) -> dict[str, Any]:
    """Load the ``devx`` configuration block from ``config/metrics.yaml``."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    block = raw.get("devx", raw)
    return dict(block) if isinstance(block, dict) else {}


def anonymize_developer(developer_id: str) -> str:
    """Deterministically anonymize a developer identifier (zero PII in metrics)."""
    cleaned = developer_id.strip().lower()
    if not cleaned:
        return "dev_anonymous"
    return f"dev_{hashlib.sha256(cleaned.encode('utf-8')).hexdigest()[:10]}"


@dataclass
class TaskEvent:
    """Lifecycle event for a developer or agent value stream task."""

    task_id: str
    stage: str
    agent: str
    event_type: str  # 'start' or 'complete'
    timestamp: datetime
    success: bool | None = None
    developer_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AIUsageEvent:
    """Single AI-assist interaction recorded for adoption and acceptance tracking."""

    agent: str
    accepted: bool
    corrected: bool = False
    cost_usd: float = 0.0
    time_saved_seconds: float = 0.0
    developer_hash: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


class DevXTracker:
    """
    Collect SPACE + DORA + AI-assist developer experience metrics and emit to Prometheus.

    Pass a custom ``registry=CollectorRegistry()`` in tests for isolated scraping,
    or leave ``registry=None`` to emit into the default global Prometheus ``REGISTRY``.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        registry: CollectorRegistry | None = None,
    ) -> None:
        raw = dict(config or {})
        if "devx" in raw and isinstance(raw["devx"], dict):
            raw = dict(raw["devx"])

        self.config: dict[str, Any] = raw
        self.enabled: bool = bool(raw.get("enabled", True))
        self.anonymize: bool = bool(raw.get("anonymize", True))
        self.aggregate_only: bool = bool(raw.get("aggregate_only", True))
        self.dimensions: dict[str, bool] = dict(
            raw.get(
                "dimensions",
                {
                    "satisfaction": True,
                    "performance": True,
                    "activity": True,
                    "communication": True,
                    "efficiency": True,
                },
            )
        )

        ai_cfg = raw.get("ai_assist", {}) if isinstance(raw.get("ai_assist"), dict) else {}
        self.track_ai_usage: bool = bool(ai_cfg.get("track_usage", True))
        self.track_ai_acceptance: bool = bool(ai_cfg.get("track_acceptance", True))
        self.track_ai_corrections: bool = bool(ai_cfg.get("track_corrections", True))

        self.registry: CollectorRegistry = registry or REGISTRY
        if registry is None:
            self.tasks_started = devx_tasks_started
            self.tasks_completed = devx_tasks_completed
            self.cycle_time_seconds = devx_cycle_time_seconds
            self.ai_usage = devx_ai_usage
            self.satisfaction_score = devx_satisfaction_score
            self.space_score = devx_space_score
            self.pr_review_turnaround = devx_pr_review_turnaround_seconds
            self.deployments_total = devx_deployments_total
        else:
            self.tasks_started = Counter(
                "devx_tasks_started_total",
                "Total developer / agent tasks started",
                ["stage", "agent"],
                registry=registry,
            )
            self.tasks_completed = Counter(
                "devx_tasks_completed_total",
                "Total developer / agent tasks completed",
                ["stage", "agent", "success"],
                registry=registry,
            )
            self.cycle_time_seconds = Histogram(
                "devx_cycle_time_seconds",
                "Task cycle time in seconds",
                ["stage"],
                buckets=_CYCLE_TIME_BUCKETS,
                registry=registry,
            )
            self.ai_usage = Counter(
                "devx_ai_usage_total",
                "AI assist invocations by agent and acceptance verdict",
                ["agent", "accepted"],
                registry=registry,
            )
            self.satisfaction_score = Gauge(
                "devx_satisfaction_score",
                "Aggregated developer satisfaction survey score (1-5)",
                ["dimension"],
                registry=registry,
            )
            self.space_score = Gauge(
                "devx_space_score",
                "Computed SPACE framework dimension score (0.0-1.0)",
                ["dimension"],
                registry=registry,
            )
            self.pr_review_turnaround = Histogram(
                "devx_pr_review_turnaround_seconds",
                "PR review turnaround time in seconds",
                buckets=_PR_REVIEW_BUCKETS,
                registry=registry,
            )
            self.deployments_total = Counter(
                "devx_deployments_total",
                "Deployments tracked for DORA deployment frequency and change failure rate",
                ["success"],
                registry=registry,
            )

        self.cycle_time_tracker = CycleTimeTracker(raw)
        self.active_tasks: dict[str, TaskEvent] = {}
        self.task_events: list[TaskEvent] = []
        self.ai_events: list[AIUsageEvent] = []
        self.satisfaction_scores: dict[str, list[float]] = {}
        self.pr_review_turnarounds: list[float] = []
        self._opted_out_hashes: set[str] = set()

    @classmethod
    def from_yaml(
        cls,
        path: Path | str,
        *,
        registry: CollectorRegistry | None = None,
    ) -> DevXTracker:
        return cls(load_devx_config(path), registry=registry)

    def opt_out(self, developer_id: str) -> None:
        """Disable tracking for a developer who voluntarily opts out."""
        self._opted_out_hashes.add(anonymize_developer(developer_id))

    def opt_in(self, developer_id: str) -> None:
        self._opted_out_hashes.discard(anonymize_developer(developer_id))

    def _resolve_developer_hash(self, developer_id: str | None) -> tuple[str | None, bool]:
        if developer_id is None:
            return None, False
        dev_hash = anonymize_developer(developer_id)
        if dev_hash in self._opted_out_hashes:
            return dev_hash, True
        return (dev_hash if self.anonymize else developer_id), False

    def start_task(
        self,
        task_id: str,
        stage: str,
        agent: str = "orchestrator",
        metadata: dict[str, Any] | None = None,
        *,
        developer_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Record task start and increment ``devx_tasks_started_total``."""
        if not self.enabled:
            return
        dev_hash, opted_out = self._resolve_developer_hash(developer_id)
        if opted_out:
            return

        ts = timestamp or datetime.now(UTC)
        event = TaskEvent(
            task_id=task_id,
            stage=stage,
            agent=agent,
            event_type="start",
            timestamp=ts,
            developer_hash=dev_hash,
            metadata=dict(metadata or {}),
        )
        self.active_tasks[f"{task_id}:{stage}"] = event
        self.active_tasks[task_id] = event
        self.task_events.append(event)
        self.cycle_time_tracker.start_stage(task_id, stage, agent, timestamp=ts)
        self.tasks_started.labels(stage=stage, agent=agent).inc()
        logger.debug("DevX: Task started - %s (%s, agent=%s)", task_id, stage, agent)

    def complete_task(
        self,
        task_id: str,
        stage: str,
        success: bool = True,
        metadata: dict[str, Any] | None = None,
        *,
        developer_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> float | None:
        """Record task completion, emit cycle time histogram, and return cycle seconds."""
        if not self.enabled:
            return None
        dev_hash, opted_out = self._resolve_developer_hash(developer_id)
        if opted_out:
            return None

        start_event = self.active_tasks.pop(f"{task_id}:{stage}", None)
        if start_event is None:
            start_event = self.active_tasks.pop(task_id, None)
        else:
            if self.active_tasks.get(task_id) is start_event:
                self.active_tasks.pop(task_id, None)

        if start_event is None:
            logger.warning("DevX: Task completion without start - %s (%s)", task_id, stage)
            return None

        end_time = timestamp or datetime.now(UTC)
        timing = self.cycle_time_tracker.complete_stage(
            task_id,
            stage,
            success=success,
            timestamp=end_time,
        )
        cycle_time = (
            timing.duration_seconds
            if timing is not None
            else max(0.0, (end_time - start_event.timestamp).total_seconds())
        )

        complete_event = TaskEvent(
            task_id=task_id,
            stage=stage,
            agent=start_event.agent,
            event_type="complete",
            timestamp=end_time,
            success=success,
            developer_hash=dev_hash or start_event.developer_hash,
            metadata=dict(metadata or {}),
        )
        self.task_events.append(complete_event)
        self.tasks_completed.labels(
            stage=stage,
            agent=start_event.agent,
            success=str(success),
        ).inc()
        self.cycle_time_seconds.labels(stage=stage).observe(cycle_time)
        logger.debug(
            "DevX: Task completed - %s (%s), cycle_time=%.2fs, success=%s",
            task_id,
            stage,
            cycle_time,
            success,
        )
        return cycle_time

    def record_ai_usage(
        self,
        agent: str,
        accepted: bool = True,
        metadata: dict[str, Any] | None = None,
        *,
        corrected: bool = False,
        cost_usd: float = 0.0,
        time_saved_seconds: float = 0.0,
        developer_id: str | None = None,
    ) -> None:
        """Record AI assist invocation, acceptance verdict, and optional correction/cost."""
        if not self.enabled or not self.track_ai_usage:
            return
        dev_hash, opted_out = self._resolve_developer_hash(developer_id)
        if opted_out:
            return

        event = AIUsageEvent(
            agent=agent,
            accepted=accepted,
            corrected=corrected,
            cost_usd=float(cost_usd),
            time_saved_seconds=float(time_saved_seconds),
            developer_hash=dev_hash,
            metadata=dict(metadata or {}),
        )
        self.ai_events.append(event)
        self.ai_usage.labels(agent=agent, accepted=str(accepted)).inc()
        logger.debug("DevX: AI usage - agent=%s accepted=%s", agent, accepted)

    def record_satisfaction(self, dimension: str, score: float) -> None:
        """Record a satisfaction rating (1..5) and update the Prometheus gauge."""
        if not self.enabled or not self.dimensions.get("satisfaction", True):
            return
        clamped = max(1.0, min(5.0, float(score)))
        bucket = self.satisfaction_scores.setdefault(dimension, [])
        bucket.append(clamped)
        self.satisfaction_score.labels(dimension=dimension).set(statistics.mean(bucket))

    def record_pr_review_turnaround(self, turnaround_seconds: float) -> None:
        """Record PR review turnaround latency (Communication dimension)."""
        if not self.enabled or not self.dimensions.get("communication", True):
            return
        val = max(0.0, float(turnaround_seconds))
        self.pr_review_turnarounds.append(val)
        self.pr_review_turnaround.observe(val)

    def record_deployment(
        self,
        *,
        success: bool = True,
        restore_time_seconds: float | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Record deployment outcome for DORA deployment frequency, CFR, and MTTR."""
        if not self.enabled:
            return
        self.cycle_time_tracker.record_deployment(
            success=success,
            restore_time_seconds=restore_time_seconds,
            timestamp=timestamp,
        )
        self.deployments_total.labels(success=str(success)).inc()

    def get_ai_effectiveness(self) -> dict[str, float]:
        """Compute aggregate AI assist adoption, acceptance, correction, and cost metrics."""
        total = len(self.ai_events)
        completed_tasks = len(self.cycle_time_tracker.completed_timings)
        if total == 0:
            return {
                "total_ai_invocations": 0.0,
                "acceptance_rate": 0.0,
                "correction_rate": 0.0,
                "ai_adoption_rate": 0.0,
                "avg_cost_per_invocation_usd": 0.0,
                "total_time_saved_seconds": 0.0,
            }

        accepted_cnt = sum(1 for e in self.ai_events if e.accepted)
        corrected_cnt = sum(1 for e in self.ai_events if e.corrected)
        avg_cost = statistics.mean(e.cost_usd for e in self.ai_events)
        time_saved = sum(e.time_saved_seconds for e in self.ai_events)
        adoption = min(1.0, total / completed_tasks) if completed_tasks > 0 else 1.0
        return {
            "total_ai_invocations": float(total),
            "acceptance_rate": round(accepted_cnt / total, 4),
            "correction_rate": round(corrected_cnt / total, 4),
            "ai_adoption_rate": round(adoption, 4),
            "avg_cost_per_invocation_usd": round(avg_cost, 6),
            "total_time_saved_seconds": round(time_saved, 2),
        }

    def get_dora_metrics(self, *, window_days: float = 7.0) -> DORAMetrics:
        return self.cycle_time_tracker.compute_dora_metrics(window_days=window_days)

    def get_developer_summary(self, developer_id: str) -> dict[str, Any]:
        """Transparency helper: let a developer inspect their own anonymized stats only."""
        dev_hash = anonymize_developer(developer_id)
        own_tasks = [
            e
            for e in self.task_events
            if e.event_type == "complete" and e.developer_hash == dev_hash
        ]
        own_ai = [e for e in self.ai_events if e.developer_hash == dev_hash]
        accepted = sum(1 for e in own_ai if e.accepted)
        return {
            "developer_hash": dev_hash,
            "opted_out": dev_hash in self._opted_out_hashes,
            "tasks_completed": len(own_tasks),
            "ai_invocations": len(own_ai),
            "ai_acceptance_rate": round(accepted / len(own_ai), 4) if own_ai else 0.0,
        }
