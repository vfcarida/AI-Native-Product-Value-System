"""
DORA-style cycle time, lead time, deployment frequency, change failure rate,
and mean-time-to-restore (MTTR) tracking (EVO-032).
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StageTiming:
    """Completed timing observation for a single task stage."""

    task_id: str
    stage: str
    agent: str
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    success: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "stage": self.stage,
            "agent": self.agent,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "duration_seconds": round(self.duration_seconds, 4),
            "success": self.success,
        }


@dataclass
class DORAMetrics:
    """Four DORA engineering delivery metrics plus Accelerate tier classification."""

    deployment_frequency_per_day: float
    lead_time_mean_seconds: float
    lead_time_p50_seconds: float
    lead_time_p95_seconds: float
    change_failure_rate: float  # 0.0 - 1.0
    mttr_seconds: float
    performance_tier: str  # 'elite', 'high', 'medium', 'low'
    stage_averages_seconds: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deployment_frequency_per_day": round(self.deployment_frequency_per_day, 4),
            "lead_time_mean_seconds": round(self.lead_time_mean_seconds, 3),
            "lead_time_p50_seconds": round(self.lead_time_p50_seconds, 3),
            "lead_time_p95_seconds": round(self.lead_time_p95_seconds, 3),
            "change_failure_rate": round(self.change_failure_rate, 4),
            "mttr_seconds": round(self.mttr_seconds, 3),
            "performance_tier": self.performance_tier,
            "stage_averages_seconds": {
                k: round(v, 3) for k, v in self.stage_averages_seconds.items()
            },
        }


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


class CycleTimeTracker:
    """Track per-stage cycle times, end-to-end lead times, and DORA metrics."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        ct_cfg = cfg.get("cycle_time", cfg)
        stages_raw = ct_cfg.get("stages", []) if isinstance(ct_cfg, dict) else []
        self.configured_stages: list[dict[str, str]] = [
            dict(s) for s in stages_raw if isinstance(s, dict)
        ]

        # Keyed by (task_id, stage) so a single task can traverse multiple stages cleanly
        self._active: dict[tuple[str, str], tuple[str, datetime]] = {}
        self.completed_timings: list[StageTiming] = []
        self.deployments: list[tuple[datetime, bool]] = []
        self.restore_durations: list[float] = []

    def start_stage(
        self,
        task_id: str,
        stage: str,
        agent: str = "unknown",
        *,
        timestamp: datetime | None = None,
    ) -> datetime:
        ts = timestamp or datetime.now(UTC)
        self._active[(task_id, stage)] = (agent, ts)
        return ts

    def complete_stage(
        self,
        task_id: str,
        stage: str,
        *,
        success: bool = True,
        timestamp: datetime | None = None,
    ) -> StageTiming | None:
        entry = self._active.pop((task_id, stage), None)
        if entry is None:
            logger.warning(
                "CycleTimeTracker: completion without matching start for task=%s stage=%s",
                task_id,
                stage,
            )
            return None

        agent, started_at = entry
        completed_at = timestamp or datetime.now(UTC)
        duration = max(0.0, (completed_at - started_at).total_seconds())
        if completed_at < started_at:
            logger.warning(
                "CycleTimeTracker: out-of-order timestamps for task=%s stage=%s; clamped to 0s",
                task_id,
                stage,
            )

        timing = StageTiming(
            task_id=task_id,
            stage=stage,
            agent=agent,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
            success=success,
        )
        self.completed_timings.append(timing)
        return timing

    def record_deployment(
        self,
        *,
        success: bool = True,
        restore_time_seconds: float | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        ts = timestamp or datetime.now(UTC)
        self.deployments.append((ts, success))
        if not success and restore_time_seconds is not None and restore_time_seconds >= 0:
            self.restore_durations.append(float(restore_time_seconds))

    def get_stage_summary(self, stage: str | None = None) -> dict[str, float]:
        samples = [
            t.duration_seconds
            for t in self.completed_timings
            if stage is None or t.stage == stage
        ]
        if not samples:
            return {"count": 0.0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
        ordered = sorted(samples)
        return {
            "count": float(len(ordered)),
            "mean": statistics.mean(ordered),
            "p50": _percentile(ordered, 50.0),
            "p95": _percentile(ordered, 95.0),
            "max": ordered[-1],
        }

    def compute_dora_metrics(self, *, window_days: float = 7.0) -> DORAMetrics:
        """Compute DORA delivery metrics over ``window_days``."""
        window = max(1.0, float(window_days))
        per_stage: dict[str, list[float]] = defaultdict(list)
        per_task_total: dict[str, float] = defaultdict(float)

        for timing in self.completed_timings:
            per_stage[timing.stage].append(timing.duration_seconds)
            per_task_total[timing.task_id] += timing.duration_seconds

        stage_avgs = {
            st: statistics.mean(vals) for st, vals in sorted(per_stage.items()) if vals
        }
        lead_times = sorted(per_task_total.values())
        lead_mean = statistics.mean(lead_times) if lead_times else 0.0
        lead_p50 = _percentile(lead_times, 50.0)
        lead_p95 = _percentile(lead_times, 95.0)

        total_deploys = len(self.deployments)
        deploy_freq = total_deploys / window
        failed_deploys = sum(1 for _, ok in self.deployments if not ok)
        cfr = (failed_deploys / total_deploys) if total_deploys > 0 else 0.0
        mttr = statistics.mean(self.restore_durations) if self.restore_durations else 0.0

        # Accelerate DORA classification (avoid fabricating a tier when zero data exists)
        if total_deploys == 0 and not lead_times:
            tier = "insufficient_data"
        elif deploy_freq >= 1.0 and lead_p50 <= 3600.0 and cfr <= 0.15:
            tier = "elite"
        elif deploy_freq >= (1.0 / 7.0) and lead_p50 <= 86400.0 and cfr <= 0.20:
            tier = "high"
        elif lead_p50 <= 7 * 86400.0 and cfr <= 0.30:
            tier = "medium"
        else:
            tier = "low"

        return DORAMetrics(
            deployment_frequency_per_day=deploy_freq,
            lead_time_mean_seconds=lead_mean,
            lead_time_p50_seconds=lead_p50,
            lead_time_p95_seconds=lead_p95,
            change_failure_rate=cfr,
            mttr_seconds=mttr,
            performance_tier=tier,
            stage_averages_seconds=stage_avgs,
        )
