"""Integration tests for EVO-032 DevX end-to-end pipeline, Grafana queries & orchestrator."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from prometheus_client import CollectorRegistry, generate_latest

from product_value_system.agents.agent import AgentResult
from product_value_system.harness.harness import Harness
from product_value_system.metrics import (
    DevXTracker,
    HttpPrometheusQueryClient,
    SatisfactionSurvey,
    SPACECalculator,
)
from product_value_system.orchestrator import (
    CheckpointManager,
    RunStatus,
    ValueStreamOrchestrator,
)
from product_value_system.policy.engine import PolicyEngine

REPO_ROOT = Path(__file__).resolve().parents[2]


async def test_end_to_end_task_lifecycle_prometheus_to_space_score() -> None:
    """End-to-end: tasks + AI usage + survey -> Prometheus exposition -> SPACE scores."""
    reg = CollectorRegistry()
    tracker = DevXTracker.from_yaml(REPO_ROOT / "config" / "metrics.yaml", registry=reg)
    survey = SatisfactionSurvey(tracker.config)

    # 1. Submit voluntary pulse survey responses & push aggregated satisfaction score
    survey.submit_response(
        {"tool_satisfaction": 4, "cognitive_load": 2, "flow_state": 4.0, "feedback": "Solid"},
        developer_id="eng1@example.org",
    )
    survey.submit_response(
        {"tool_satisfaction": 5, "cognitive_load": 2, "flow_state": 5.0, "feedback": "Fast"},
        developer_id="eng2@example.org",
    )
    survey_agg = survey.aggregate()
    tracker.record_satisfaction(
        "tool_satisfaction",
        survey_agg.rating_averages["tool_satisfaction"],
    )

    # 2. Run multi-stage tasks + AI usage + PR review + deployment
    t0 = datetime(2026, 11, 22, 10, 0, 0, tzinfo=UTC)
    for i in range(10):
        for stage, secs in (
            ("discovery", 120),
            ("specification", 180),
            ("build", 240),
            ("review", 90),
            ("deploy", 60),
        ):
            tracker.start_task(f"feat-{i}", stage=stage, agent=f"{stage}-agent", timestamp=t0)
            tracker.complete_task(
                f"feat-{i}",
                stage=stage,
                success=True,
                timestamp=t0 + timedelta(seconds=secs),
            )
            tracker.record_ai_usage(f"{stage}-agent", accepted=True, cost_usd=0.02)
        tracker.record_pr_review_turnaround(2400.0)
        tracker.record_deployment(success=True)

    # 3. Compute SPACE scores and verify Prometheus scrape output contains every metric family
    calc = SPACECalculator(tracker=tracker, weekly_task_target=50.0)
    scores = await calc.compute_space_scores()
    assert 0.85 <= scores["overall"] <= 1.0
    assert scores["satisfaction"] == pytest.approx(0.9)
    assert scores["activity"] == pytest.approx(1.0)
    assert scores["efficiency"] == pytest.approx(1.0)

    exposition = generate_latest(reg).decode("utf-8")
    for metric_name in (
        "devx_tasks_started_total",
        "devx_tasks_completed_total",
        "devx_cycle_time_seconds_bucket",
        "devx_ai_usage_total",
        "devx_satisfaction_score",
        "devx_space_score",
        "devx_pr_review_turnaround_seconds_bucket",
        "devx_deployments_total",
    ):
        assert metric_name in exposition, f"Missing {metric_name} in Prometheus output"


async def test_grafana_dashboard_queries_match_emitted_prometheus_metrics() -> None:
    """Validate Grafana dashboard JSON structure and verify every PromQL metric is emitted."""
    dash_paths = [
        REPO_ROOT / "dashboards" / "devx_dashboard.json",
        REPO_ROOT / "monitoring" / "grafana" / "dashboards" / "devx_dashboard.json",
    ]
    for path in dash_paths:
        assert path.exists(), f"Missing Grafana dashboard: {path}"

    primary = json.loads(dash_paths[0].read_text(encoding="utf-8"))
    provisioned = json.loads(dash_paths[1].read_text(encoding="utf-8"))
    assert primary == provisioned
    assert primary["uid"] == "pvs-devx-space"
    assert len(primary["panels"]) >= 6

    # Emit one complete lifecycle into an isolated registry
    reg = CollectorRegistry()
    tracker = DevXTracker({"enabled": True}, registry=reg)
    t0 = datetime.now(UTC)
    tracker.start_task("dash-1", stage="discovery", agent="disc", timestamp=t0)
    tracker.complete_task(
        "dash-1", stage="discovery", success=True, timestamp=t0 + timedelta(seconds=100)
    )
    tracker.record_ai_usage("disc", accepted=True)
    tracker.record_satisfaction("tool_satisfaction", 4.2)
    tracker.record_pr_review_turnaround(1500.0)
    tracker.record_deployment(success=True)
    await SPACECalculator(tracker=tracker).compute_space_scores()

    exposition = generate_latest(reg).decode("utf-8")

    # Every devx_* metric referenced in any panel PromQL expression MUST exist in exposition
    metric_ref_re = re.compile(r"\b(devx_[a-z0-9_]+)\b")
    checked_metrics: set[str] = set()
    for panel in primary["panels"]:
        for target in panel.get("targets", []):
            expr = str(target.get("expr", ""))
            assert expr.strip()
            for metric_name in metric_ref_re.findall(expr):
                checked_metrics.add(metric_name)
                assert metric_name in exposition, (
                    f"Dashboard panel '{panel['title']}' references '{metric_name}' "
                    "which is not emitted by DevXTracker"
                )

    assert len(checked_metrics) >= 6


async def test_orchestrator_emits_devx_metrics_end_to_end(tmp_path: Path) -> None:
    """ValueStreamOrchestrator automatically emits DevX stage, AI, and deploy metrics."""

    class _FastAgent:
        def __init__(self, name: str, output: dict[str, Any]) -> None:
            self.name = name
            self.output = output

        async def execute(self, _inp: Any, _harness: Any, _ctx: Any) -> AgentResult:
            return AgentResult(
                success=True,
                output_artifact=dict(self.output),
                cost_usd=0.01,
                iterations=1,
                duration_seconds=0.01,
                reasoning_trace=[],
                tool_calls=[],
                checkpoints=[],
            )

    reg = CollectorRegistry()
    tracker = DevXTracker.from_yaml(REPO_ROOT / "config" / "metrics.yaml", registry=reg)
    policy = PolicyEngine()
    shared_harness = Harness(policy_engine=policy)

    orch = ValueStreamOrchestrator(
        agents={
            "discovery": _FastAgent("discovery-agent", {"id": "out-1", "hypothesis": "Faster"}),
            "specification": _FastAgent("spec-agent", {"id": "spec-1", "requirements": ["R1"]}),
            "build": _FastAgent("build-agent", {"files": ["app.py"]}),
        },
        evaluation_pipeline=None,
        policy_engine=policy,
        checkpoint_manager=CheckpointManager(tmp_path / "cp"),
        artifact_root=tmp_path / "art",
        harness_factory=lambda _s: shared_harness,
        devx_tracker=tracker,
    )

    problem = {
        "id": "prob-devx-001",
        "version": 1,
        "created": datetime.now(UTC).isoformat(),
        "statement": "Verify DevX metrics instrumentation during value stream execution",
    }
    run = await orch.execute_run(problem, config={"user_id": "alice@example.org"})

    assert run.status == RunStatus.COMPLETED
    assert len(tracker.cycle_time_tracker.completed_timings) == 10
    assert tracker.get_ai_effectiveness()["total_ai_invocations"] >= 3
    assert tracker.get_ai_effectiveness()["acceptance_rate"] == pytest.approx(1.0)
    assert reg.get_sample_value("devx_deployments_total", {"success": "True"}) == pytest.approx(1.0)
    # Privacy check: raw user email never leaks into Prometheus exposition
    assert "alice@example.org" not in generate_latest(reg).decode("utf-8")


async def test_http_prometheus_query_client_with_space_calculator() -> None:
    """SPACECalculator works against HttpPrometheusQueryClient (/api/v1/query)."""

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("query", "")
        if "devx_satisfaction_score" in q:
            val = "4.0"
        elif 'success="True"' in q:
            val = "40"
        elif "devx_tasks_completed_total" in q:
            val = "40"
        elif 'accepted="True"' in q:
            val = "18"
        elif "devx_ai_usage_total" in q:
            val = "20"
        elif "devx_pr_review_turnaround_seconds" in q:
            val = "3600"
        elif "devx_cycle_time_seconds" in q:
            val = "300"
        else:
            return httpx.Response(200, json={"status": "success", "data": {"result": []}})
        return httpx.Response(
            200,
            json={"status": "success", "data": {"result": [{"value": [1700000000, val]}]}},
        )

    client = HttpPrometheusQueryClient(
        "http://prometheus.local:9090",
        transport=httpx.MockTransport(handler),
    )
    calc = SPACECalculator(prometheus_client=client, weekly_task_target=50.0)
    scores = await calc.compute_space_scores()

    assert scores["satisfaction"] == pytest.approx(0.8)
    assert scores["performance"] == pytest.approx(0.95)  # avg(40/40, 18/20)
    assert scores["activity"] == pytest.approx(0.8)  # 40/50
    assert scores["communication"] == pytest.approx(1.0)  # 3600s == excellent
    assert scores["efficiency"] == pytest.approx(1.0)  # 300s == excellent
    assert scores["overall"] == pytest.approx((0.8 + 0.95 + 0.8 + 1.0 + 1.0) / 5.0)
