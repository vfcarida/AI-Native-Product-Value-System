"""
SPACE framework calculator aggregating Satisfaction, Performance, Activity,
Communication, and Efficiency dimensions from Prometheus or in-process DevXTracker (EVO-032).
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from product_value_system.metrics.devx_tracker import DevXTracker

logger = logging.getLogger(__name__)


@runtime_checkable
class PrometheusQueryClient(Protocol):
    """Protocol for executing instant PromQL queries returning a scalar float."""

    async def query(self, promql: str) -> float | None:
        ...


@dataclass
class SPACEScores:
    """Normalized 0.0–1.0 SPACE developer productivity profile."""

    satisfaction: float
    performance: float
    activity: float
    communication: float
    efficiency: float
    overall: float

    def to_dict(self) -> dict[str, float]:
        return {
            "satisfaction": round(self.satisfaction, 4),
            "performance": round(self.performance, 4),
            "activity": round(self.activity, 4),
            "communication": round(self.communication, 4),
            "efficiency": round(self.efficiency, 4),
            "overall": round(self.overall, 4),
        }


class HttpPrometheusQueryClient:
    """Async HTTP client querying a live Prometheus server via ``/api/v1/query``."""

    def __init__(
        self,
        base_url: str = "http://localhost:9090",
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    async def query(self, promql: str) -> float | None:
        kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "timeout": httpx.Timeout(self.timeout),
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get("/api/v1/query", params={"query": promql})
            if resp.status_code >= 400:
                logger.warning("Prometheus query failed (%d): %s", resp.status_code, promql)
                return None
            payload = resp.json()
            results = (payload.get("data") or {}).get("result") or []
            if not results:
                return None
            raw_val = results[0].get("value", [None, None])[1]
            try:
                return float(raw_val)
            except (TypeError, ValueError):
                return None


class LocalDevXQueryAdapter:
    """
    In-process ``PrometheusQueryClient`` backed directly by a ``DevXTracker`` instance.

    Enables instant SPACE score computation and offline validation without an external
    Prometheus server while matching the exact PromQL semantics used in Grafana dashboards.
    """

    def __init__(self, tracker: DevXTracker) -> None:
        self.tracker = tracker

    async def query(self, promql: str) -> float | None:
        q = promql.strip()

        if "devx_satisfaction_score" in q:
            if 'dimension="tool_satisfaction"' in q:
                vals = self.tracker.satisfaction_scores.get("tool_satisfaction", [])
                return statistics.mean(vals) if vals else None
            all_vals = [v for bucket in self.tracker.satisfaction_scores.values() for v in bucket]
            return statistics.mean(all_vals) if all_vals else None

        if "devx_tasks_completed_total" in q and 'success="True"' in q:
            return float(
                sum(1 for t in self.tracker.cycle_time_tracker.completed_timings if t.success)
            )

        if "devx_tasks_completed_total" in q:
            count = len(self.tracker.cycle_time_tracker.completed_timings)
            return float(count) if count > 0 else None

        if "devx_ai_usage_total" in q and 'accepted="True"' in q:
            return float(sum(1 for e in self.tracker.ai_events if e.accepted))

        if "devx_ai_usage_total" in q:
            return float(len(self.tracker.ai_events))

        if "devx_pr_review_turnaround_seconds" in q:
            vals = self.tracker.pr_review_turnarounds
            return statistics.mean(vals) if vals else None

        if "devx_cycle_time_seconds" in q:
            timings = self.tracker.cycle_time_tracker.completed_timings
            for stage_name in ("discovery", "specification", "build", "review", "deploy"):
                if f'stage="{stage_name}"' in q:
                    stage_vals = [t.duration_seconds for t in timings if t.stage == stage_name]
                    return statistics.mean(stage_vals) if stage_vals else None
            return statistics.mean(t.duration_seconds for t in timings) if timings else None

        return None


class SPACECalculator:
    """Compute normalized SPACE framework scores (0.0–1.0) from DevX telemetry."""

    def __init__(
        self,
        prometheus_client: PrometheusQueryClient | None = None,
        *,
        tracker: DevXTracker | None = None,
        weekly_task_target: float = 50.0,
        excellent_cycle_seconds: float = 300.0,
        poor_cycle_seconds: float = 1800.0,
        excellent_pr_review_seconds: float = 3600.0,
        poor_pr_review_seconds: float = 86400.0,
    ) -> None:
        if prometheus_client is not None:
            self.prom: PrometheusQueryClient = prometheus_client
        elif tracker is not None:
            self.prom = LocalDevXQueryAdapter(tracker)
        else:
            raise ValueError("SPACECalculator requires either prometheus_client or tracker.")
        self.tracker = tracker
        self.weekly_task_target = max(1.0, float(weekly_task_target))
        self.excellent_cycle_seconds = float(excellent_cycle_seconds)
        self.poor_cycle_seconds = max(
            self.excellent_cycle_seconds + 1.0, float(poor_cycle_seconds)
        )
        self.excellent_pr_review_seconds = float(excellent_pr_review_seconds)
        self.poor_pr_review_seconds = max(
            self.excellent_pr_review_seconds + 1.0, float(poor_pr_review_seconds)
        )

    async def compute_space_scores(self) -> dict[str, float]:
        """Compute all 5 SPACE dimensions + ``overall`` average across observed dimensions.

        Unobserved dimensions return ``0.0`` rather than fabricated neutral constants;
        ``overall`` averages only dimensions with actual observations (``0.0`` when empty).
        """
        raw_dims = (
            await self._query_satisfaction(),
            await self._query_performance(),
            await self._query_activity(),
            await self._query_communication(),
            await self._query_efficiency(),
        )
        observed = [val for val in raw_dims if val is not None]
        overall = (sum(observed) / len(observed)) if observed else 0.0

        scores = SPACEScores(
            satisfaction=raw_dims[0] if raw_dims[0] is not None else 0.0,
            performance=raw_dims[1] if raw_dims[1] is not None else 0.0,
            activity=raw_dims[2] if raw_dims[2] is not None else 0.0,
            communication=raw_dims[3] if raw_dims[3] is not None else 0.0,
            efficiency=raw_dims[4] if raw_dims[4] is not None else 0.0,
            overall=overall,
        )

        # Publish SPACE dimension gauges back to Prometheus if tracker is attached
        if self.tracker is not None and self.tracker.enabled:
            for dim, val in scores.to_dict().items():
                self.tracker.space_score.labels(dimension=dim).set(val)

        return scores.to_dict()

    async def _query_satisfaction(self) -> float | None:
        query = 'avg_over_time(devx_satisfaction_score{dimension="tool_satisfaction"}[7d])'
        res = await self.prom.query(query)
        if res is None:
            return None
        return max(0.0, min(1.0, res / 5.0))

    async def _query_performance(self) -> float | None:
        total_completed = await self.prom.query("sum(increase(devx_tasks_completed_total[7d]))")
        succeeded = await self.prom.query(
            'sum(increase(devx_tasks_completed_total{success="True"}[7d]))'
        )
        ai_total = await self.prom.query("sum(increase(devx_ai_usage_total[7d]))")
        ai_accepted = await self.prom.query(
            'sum(increase(devx_ai_usage_total{accepted="True"}[7d]))'
        )

        components: list[float] = []
        if total_completed and total_completed > 0 and succeeded is not None:
            components.append(max(0.0, min(1.0, succeeded / total_completed)))
        if ai_total and ai_total > 0 and ai_accepted is not None:
            components.append(max(0.0, min(1.0, ai_accepted / ai_total)))

        if not components:
            return None
        return sum(components) / len(components)

    async def _query_activity(self) -> float | None:
        res = await self.prom.query("sum(increase(devx_tasks_completed_total[7d]))")
        if res is None or res <= 0:
            return None
        return max(0.0, min(1.0, res / self.weekly_task_target))

    async def _query_communication(self) -> float | None:
        res = await self.prom.query("avg(devx_pr_review_turnaround_seconds)")
        if res is None:
            return None
        if res <= self.excellent_pr_review_seconds:
            return 1.0
        if res >= self.poor_pr_review_seconds:
            return 0.3
        span = self.poor_pr_review_seconds - self.excellent_pr_review_seconds
        return 1.0 - ((res - self.excellent_pr_review_seconds) / span) * 0.7

    async def _query_efficiency(self) -> float | None:
        res = await self.prom.query("avg(devx_cycle_time_seconds)")
        if res is None:
            res = await self.prom.query('avg(devx_cycle_time_seconds{stage="discovery"})')
        if res is None:
            return None
        if res <= self.excellent_cycle_seconds:
            return 1.0
        if res >= self.poor_cycle_seconds:
            return 0.3
        span = self.poor_cycle_seconds - self.excellent_cycle_seconds
        return 1.0 - ((res - self.excellent_cycle_seconds) / span) * 0.7
