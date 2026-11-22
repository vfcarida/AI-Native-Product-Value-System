"""
OpenTelemetry & Prometheus metrics instrumentation for the Product Value System (EVO-014).

Exposes 12 low-cardinality production metrics covering latency, error rate,
availability, throughput, saturation, quality scores, USD cost, tokens,
hallucination rate, policy violations, and remaining error budgets.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from typing import Any

from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader

logger = logging.getLogger(__name__)


class PVSMetrics:
    """
    Production metrics collector backed by OpenTelemetry + Prometheus exporter
    with a thread-safe in-process snapshot for fast SLO evaluation and testing.
    """

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        enable_prometheus: bool = True,
        register_global_prometheus: bool = False,
        max_samples: int = 10000,
    ) -> None:
        if enabled is None:
            env_val = os.getenv("PVS_ENABLE_METRICS", "1").strip().lower()
            enabled = env_val not in {"0", "false", "no", "off"}
        self.enabled = enabled
        self._lock = threading.RLock()
        self._prometheus_reader: Any = None

        readers: list[MetricReader] = []
        if self.enabled and enable_prometheus and register_global_prometheus:
            try:
                from opentelemetry.exporter.prometheus import PrometheusMetricReader

                self._prometheus_reader = PrometheusMetricReader()
                readers.append(self._prometheus_reader)
            except ImportError:
                logger.debug("Prometheus exporter not installed; using in-process metrics only")

        self.meter_provider = MeterProvider(metric_readers=readers)
        self.meter = self.meter_provider.get_meter("product_value_system.monitoring", "0.2.0")

        # 12 OpenTelemetry instruments (low-cardinality labels: agent, status, model, slo)
        self.request_counter = self.meter.create_counter(
            name="pvs.requests.total",
            description="Total agent/model requests by agent and status",
            unit="1",
        )
        self.error_counter = self.meter.create_counter(
            name="pvs.errors.total",
            description="Total errors by agent and error_type",
            unit="1",
        )
        self.latency_histogram = self.meter.create_histogram(
            name="pvs.latency.seconds",
            description="Model/agent invocation latency distribution in seconds",
            unit="s",
        )
        self.cost_counter = self.meter.create_counter(
            name="pvs.cost.usd.total",
            description="Cumulative USD spend by agent and model",
            unit="USD",
        )
        self.token_counter = self.meter.create_counter(
            name="pvs.tokens.total",
            description="Cumulative tokens consumed by agent and model",
            unit="1",
        )
        self.quality_histogram = self.meter.create_histogram(
            name="pvs.quality.score",
            description="Evaluation quality score distribution (0.0-1.0)",
            unit="1",
        )
        self.eval_counter = self.meter.create_counter(
            name="pvs.eval.executions.total",
            description="Evaluation pipeline runs by pass/fail verdict",
            unit="1",
        )
        self.agent_counter = self.meter.create_counter(
            name="pvs.agent.executions.total",
            description="Completed agent executions by agent and status",
            unit="1",
        )
        self.error_budget_counter = self.meter.create_up_down_counter(
            name="pvs.error_budget.remaining",
            description="Error budget remaining per SLO (0.0-1.0)",
            unit="1",
        )
        self.hallucination_histogram = self.meter.create_histogram(
            name="pvs.hallucination.rate",
            description="Observed hallucination rate per evaluation (0.0-1.0)",
            unit="1",
        )
        self.policy_violation_counter = self.meter.create_counter(
            name="pvs.policy.violations.total",
            description="Policy/security violations by agent and decision",
            unit="1",
        )
        self.active_runs_counter = self.meter.create_up_down_counter(
            name="pvs.active.runs",
            description="Currently executing value stream runs (saturation)",
            unit="1",
        )

        # In-process aggregation state for fast SLO evaluation & Prometheus fallback
        self._max_samples = max_samples
        self.reset_snapshots()

    def reset_snapshots(self) -> None:
        """Reset in-process counters and rolling windows (useful between tests)."""
        with self._lock:
            self._requests_success = 0
            self._requests_error = 0
            self._errors_by_type: dict[str, int] = {}
            self._latencies: deque[float] = deque(maxlen=self._max_samples)
            self._total_cost_usd = 0.0
            self._cost_by_agent: dict[str, float] = {}
            self._total_tokens = 0
            self._eval_passed = 0
            self._eval_failed = 0
            self._quality_scores: deque[float] = deque(maxlen=self._max_samples)
            self._hallucination_rates: deque[float] = deque(maxlen=self._max_samples)
            self._agent_executions_success = 0
            self._agent_executions_error = 0
            self._policy_violations = 0
            self._active_runs = 0
            self._error_budgets: dict[str, float] = {}

    def record_request(
        self,
        *,
        agent: str,
        status: str,
        latency_seconds: float | None = None,
        cost_usd: float = 0.0,
        tokens: int = 0,
        model: str = "unknown",
        error_type: str | None = None,
    ) -> None:
        """Record a single model/agent request with optional latency, cost, and error."""
        if not self.enabled:
            return

        self.request_counter.add(1, attributes={"agent": agent, "status": status})
        if latency_seconds is not None:
            self.latency_histogram.record(latency_seconds, attributes={"agent": agent})
        if cost_usd > 0.0:
            self.cost_counter.add(cost_usd, attributes={"agent": agent, "model": model})
        if tokens > 0:
            self.token_counter.add(tokens, attributes={"agent": agent, "model": model})
        if status == "error":
            self.error_counter.add(
                1,
                attributes={"agent": agent, "error_type": error_type or "Error"},
            )

        with self._lock:
            if status == "success":
                self._requests_success += 1
            elif status == "error":
                self._requests_error += 1
                err_key = error_type or "Error"
                self._errors_by_type[err_key] = self._errors_by_type.get(err_key, 0) + 1
            if latency_seconds is not None:
                self._latencies.append(float(latency_seconds))
            if cost_usd > 0.0:
                self._total_cost_usd += float(cost_usd)
                self._cost_by_agent[agent] = self._cost_by_agent.get(agent, 0.0) + float(cost_usd)
            if tokens > 0:
                self._total_tokens += int(tokens)

    def record_agent_execution(
        self,
        *,
        agent: str,
        status: str,
        cost_usd: float = 0.0,
    ) -> None:
        """Record completion of a stage/agent execution."""
        if not self.enabled:
            return
        self.agent_counter.add(1, attributes={"agent": agent, "status": status})
        with self._lock:
            if status == "success":
                self._agent_executions_success += 1
            else:
                self._agent_executions_error += 1
            if cost_usd > 0.0:
                self._total_cost_usd += float(cost_usd)
                self._cost_by_agent[agent] = self._cost_by_agent.get(agent, 0.0) + float(cost_usd)

    def record_evaluation(
        self,
        *,
        artifact_type: str,
        score: float,
        passed: bool,
        hallucination_rate: float | None = None,
    ) -> None:
        """Record an evaluation pipeline outcome and quality score."""
        if not self.enabled:
            return
        self.quality_histogram.record(score, attributes={"artifact_type": artifact_type})
        self.eval_counter.add(
            1,
            attributes={"artifact_type": artifact_type, "passed": str(passed).lower()},
        )
        if hallucination_rate is not None:
            self.hallucination_histogram.record(
                hallucination_rate,
                attributes={"artifact_type": artifact_type},
            )
        with self._lock:
            self._quality_scores.append(float(score))
            if passed:
                self._eval_passed += 1
            else:
                self._eval_failed += 1
            if hallucination_rate is not None:
                self._hallucination_rates.append(float(hallucination_rate))

    def record_policy_violation(self, *, agent: str, decision: str = "DENY") -> None:
        """Record a policy or security violation."""
        if not self.enabled:
            return
        self.policy_violation_counter.add(
            1,
            attributes={"agent": agent, "decision": decision},
        )
        with self._lock:
            self._policy_violations += 1

    def set_error_budget(self, slo_name: str, remaining: float) -> None:
        """Update remaining error budget snapshot for ``slo_name``."""
        if not self.enabled:
            return
        with self._lock:
            previous = self._error_budgets.get(slo_name, 0.0)
            delta = float(remaining) - previous
            self._error_budgets[slo_name] = float(remaining)
        self.error_budget_counter.add(delta, attributes={"slo": slo_name})

    def track_active_run(self, delta: int) -> None:
        """Adjust active concurrent run count (+1 on start, -1 on completion)."""
        if not self.enabled:
            return
        self.active_runs_counter.add(delta)
        with self._lock:
            self._active_runs = max(0, self._active_runs + delta)

    @staticmethod
    def _quantile_from_sorted(ordered: list[float], quantile: float) -> float:
        if not ordered:
            return 0.0
        idx = min(len(ordered) - 1, max(0, int(len(ordered) * quantile)))
        return round(ordered[idx], 6)

    def compute_latency_quantile(self, quantile: float) -> float:
        """Compute latency percentile (e.g., 0.50, 0.95, 0.99) in seconds."""
        with self._lock:
            ordered = sorted(self._latencies)
        return self._quantile_from_sorted(ordered, quantile)

    def get_snapshot(self) -> dict[str, Any]:
        """Return aggregated metrics snapshot for SLO evaluation and JSON APIs."""
        with self._lock:
            total_reqs = self._requests_success + self._requests_error
            availability = self._requests_success / total_reqs if total_reqs > 0 else 1.0
            error_rate = self._requests_error / total_reqs if total_reqs > 0 else 0.0

            total_evals = self._eval_passed + self._eval_failed
            eval_pass_rate = self._eval_passed / total_evals if total_evals > 0 else 1.0
            mean_quality = (
                sum(self._quality_scores) / len(self._quality_scores)
                if self._quality_scores
                else 1.0
            )

            total_execs = self._agent_executions_success + self._agent_executions_error
            denom_outcomes = max(1, self._agent_executions_success or self._requests_success)
            avg_cost_per_outcome = (
                self._total_cost_usd / denom_outcomes
                if (self._agent_executions_success or self._requests_success) > 0
                else 0.0
            )

            mean_hallucination = (
                sum(self._hallucination_rates) / len(self._hallucination_rates)
                if self._hallucination_rates
                else 0.0
            )
            safety_denom = max(1, total_execs or total_reqs)
            safety_compliance = (
                max(0.0, 1.0 - (self._policy_violations / safety_denom))
                if (total_execs or total_reqs) > 0
                else 1.0
            )
            ordered_latencies = sorted(self._latencies)

            snapshot = {
                "requests_total": total_reqs,
                "requests_success": self._requests_success,
                "requests_error": self._requests_error,
                "availability": round(availability, 6),
                "error_rate": round(error_rate, 6),
                "latency_p50_seconds": self._quantile_from_sorted(ordered_latencies, 0.50),
                "latency_p95_seconds": self._quantile_from_sorted(ordered_latencies, 0.95),
                "latency_p99_seconds": self._quantile_from_sorted(ordered_latencies, 0.99),
                "total_cost_usd": round(self._total_cost_usd, 6),
                "avg_cost_per_outcome_usd": round(avg_cost_per_outcome, 6),
                "cost_by_agent": dict(self._cost_by_agent),
                "total_tokens": self._total_tokens,
                "eval_total": total_evals,
                "eval_pass_rate": round(eval_pass_rate, 6),
                "mean_quality_score": round(mean_quality, 6),
                "hallucination_free_rate": round(1.0 - mean_hallucination, 6),
                "policy_violations": self._policy_violations,
                "safety_compliance": round(safety_compliance, 6),
                "active_runs": self._active_runs,
                "error_budgets": dict(self._error_budgets),
            }
        return snapshot

    def render_prometheus_metrics(self) -> tuple[bytes, str]:
        """Render current metrics in standard Prometheus text exposition format."""
        snap = self.get_snapshot()
        lines = [
            "# HELP pvs_requests_total Total agent/model requests by status",
            "# TYPE pvs_requests_total counter",
            f'pvs_requests_total{{status="success"}} {snap["requests_success"]}',
            f'pvs_requests_total{{status="error"}} {snap["requests_error"]}',
            "# HELP pvs_errors_total Total agent/model errors",
            "# TYPE pvs_errors_total counter",
            f'pvs_errors_total {snap["requests_error"]}',
            "# HELP pvs_latency_p95_seconds 95th percentile request latency in seconds",
            "# TYPE pvs_latency_p95_seconds gauge",
            f'pvs_latency_p95_seconds {snap["latency_p95_seconds"]}',
            "# HELP pvs_cost_usd_total Cumulative LLM and agent USD spend",
            "# TYPE pvs_cost_usd_total counter",
            f'pvs_cost_usd_total {snap["total_cost_usd"]}',
            "# HELP pvs_tokens_total Cumulative tokens consumed",
            "# TYPE pvs_tokens_total counter",
            f'pvs_tokens_total {snap["total_tokens"]}',
            "# HELP pvs_quality_pass_rate Evaluation quality gate pass rate (0-1)",
            "# TYPE pvs_quality_pass_rate gauge",
            f'pvs_quality_pass_rate {snap["eval_pass_rate"]}',
            "# HELP pvs_hallucination_free_rate Hallucination-free fact rate (0-1)",
            "# TYPE pvs_hallucination_free_rate gauge",
            f'pvs_hallucination_free_rate {snap["hallucination_free_rate"]}',
            "# HELP pvs_policy_violations_total Total policy/security violations",
            "# TYPE pvs_policy_violations_total counter",
            f'pvs_policy_violations_total {snap["policy_violations"]}',
            "# HELP pvs_active_runs Currently active concurrent value stream runs",
            "# TYPE pvs_active_runs gauge",
            f'pvs_active_runs {snap["active_runs"]}',
        ]
        for slo_name, remaining in sorted(snap["error_budgets"].items()):
            lines.append(f'pvs_error_budget_remaining{{slo="{slo_name}"}} {remaining}')

        body = ("\n".join(lines) + "\n").encode("utf-8")
        try:
            from prometheus_client import CONTENT_TYPE_LATEST

            return body, CONTENT_TYPE_LATEST
        except ImportError:
            return body, "text/plain; version=0.0.4; charset=utf-8"


_GLOBAL_METRICS: PVSMetrics | None = None
_GLOBAL_LOCK = threading.Lock()


def get_metrics() -> PVSMetrics:
    """Return process-wide ``PVSMetrics`` singleton (lazy initialization)."""
    global _GLOBAL_METRICS
    with _GLOBAL_LOCK:
        if _GLOBAL_METRICS is None:
            _GLOBAL_METRICS = PVSMetrics(register_global_prometheus=True)
            try:
                otel_metrics.set_meter_provider(_GLOBAL_METRICS.meter_provider)
            except Exception as exc:
                logger.debug("Global OTel meter provider already configured: %s", exc)
        return _GLOBAL_METRICS


def reset_metrics() -> PVSMetrics:
    """Reset snapshots on the process-wide ``PVSMetrics`` singleton."""
    m = get_metrics()
    m.reset_snapshots()
    return m
