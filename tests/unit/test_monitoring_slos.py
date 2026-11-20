"""
Unit tests for EVO-014: Production Monitoring, SLIs, SLOs, Error Budgets & Metrics.
"""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from product_value_system.evaluation.judge import Judge, JudgeResult
from product_value_system.evaluation.pipeline import EvaluationPipeline
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.monitoring.error_budget import (
    DEFAULT_ERROR_BUDGET_ACTIONS,
    ErrorBudgetPolicy,
)
from product_value_system.monitoring.metrics import PVSMetrics, get_metrics, reset_metrics
from product_value_system.monitoring.sli import (
    AI_SLIS,
    AVAILABILITY_SLI,
    COST_PER_OUTCOME_SLI,
    DEFAULT_SLIS,
    HALLUCINATION_FREE_SLI,
    LATENCY_P95_SLI,
    QUALITY_SCORE_SLI,
    TRADITIONAL_SLIS,
    SLIType,
)
from product_value_system.monitoring.slo import (
    AVAILABILITY_SLO,
    COST_SLO,
    DEFAULT_SLOS,
    LATENCY_SLO,
    QUALITY_SLO,
    SLO,
)
from product_value_system.monitoring.slo_evaluator import SLOEvaluator
from product_value_system.security.secret_detection import SecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]


class _ConstantJudge(Judge):
    """Minimal deterministic judge for testing evaluation metrics emission."""

    def __init__(self, score: float, passed: bool) -> None:
        self._score = score
        self._passed = passed

    @property
    def name(self) -> str:
        return "constant-test-judge"

    @property
    def judge_type(self) -> str:
        return "deterministic"

    async def evaluate(
        self,
        artifact: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> JudgeResult:
        artifact_type = str(context.get("artifact_type", "unknown")) if context else "unknown"
        return JudgeResult(
            judge_name=self.name,
            artifact_type=artifact_type,
            passed=self._passed,
            score=self._score,
            rationale="Deterministic test score",
        )


@pytest.mark.unit
def test_default_slis_cover_5_traditional_and_3_ai_specific() -> None:
    """Verify catalog defines 5 traditional SLIs and >=3 AI-specific SLIs."""
    assert len(TRADITIONAL_SLIS) == 5
    assert {"availability", "latency_p95", "error_rate", "throughput", "saturation"} == set(
        TRADITIONAL_SLIS.keys()
    )

    assert len(AI_SLIS) >= 3
    assert {"quality_score", "cost_per_outcome", "hallucination_free_rate"}.issubset(
        set(AI_SLIS.keys())
    )
    assert AVAILABILITY_SLI.type == SLIType.AVAILABILITY
    assert QUALITY_SCORE_SLI.type == SLIType.QUALITY_SCORE
    assert COST_PER_OUTCOME_SLI.type == SLIType.COST
    assert HALLUCINATION_FREE_SLI.type == SLIType.HALLUCINATION
    assert len(DEFAULT_SLIS) == len(TRADITIONAL_SLIS) + len(AI_SLIS)


@pytest.mark.unit
def test_slo_error_budget_calculation() -> None:
    """99.9% SLO yields 0.1% (0.001) error budget."""
    slo = SLO(
        name="availability_999",
        sli=AVAILABILITY_SLI,
        target=0.999,
        measurement_window="30d",
    )
    assert slo.compute_error_budget() == pytest.approx(0.001)
    assert AVAILABILITY_SLO.compute_error_budget() == pytest.approx(0.01)
    assert QUALITY_SLO.compute_error_budget() == pytest.approx(0.05)


@pytest.mark.unit
def test_slo_budget_remaining_higher_is_better_and_clamping() -> None:
    """Verify budget remaining ratio and [0.0, 1.0] clamping for ratio SLIs."""
    slo = SLO(
        name="availability",
        sli=AVAILABILITY_SLI,
        target=0.99,
        measurement_window="30d",
    )
    # Exact 50% budget consumed (0.005 of 0.01)
    assert slo.compute_budget_remaining(0.985) == pytest.approx(0.5)
    assert not slo.is_met(0.985)

    # Exceeding target clamps to 1.0
    assert slo.compute_budget_remaining(0.998) == pytest.approx(1.0)
    assert slo.is_met(0.998)

    # Worse than full budget clamps to 0.0
    assert slo.compute_budget_remaining(0.95) == pytest.approx(0.0)


@pytest.mark.unit
def test_slo_lower_is_better_latency_and_cost() -> None:
    """Verify error budget math for lower-is-better SLIs (latency p95, cost)."""
    assert LATENCY_SLO.sli is LATENCY_P95_SLI
    assert LATENCY_SLO.compute_error_budget() == pytest.approx(5.0)  # 10.0 - 5.0
    assert LATENCY_SLO.compute_budget_remaining(3.0) == pytest.approx(1.0)
    assert LATENCY_SLO.is_met(5.0)
    assert LATENCY_SLO.compute_budget_remaining(7.5) == pytest.approx(0.5)
    assert LATENCY_SLO.compute_budget_remaining(12.0) == pytest.approx(0.0)
    assert not LATENCY_SLO.is_met(7.5)

    assert COST_SLO.compute_error_budget() == pytest.approx(0.50)  # 1.00 - 0.50
    assert COST_SLO.compute_budget_remaining(0.40) == pytest.approx(1.0)
    assert COST_SLO.compute_budget_remaining(0.75) == pytest.approx(0.5)
    assert COST_SLO.compute_budget_remaining(1.50) == pytest.approx(0.0)


@pytest.mark.unit
def test_error_budget_policy_actions() -> None:
    """Verify policy action selection across 50%, 25%, and 0% budget tiers."""
    policy = ErrorBudgetPolicy(
        slo=AVAILABILITY_SLO,
        actions=dict(DEFAULT_ERROR_BUDGET_ACTIONS),
    )
    assert "Continue" in policy.get_action(0.60)
    assert "Continue" in policy.get_action(0.50)
    assert "Suspend" in policy.get_action(0.25)
    assert "Suspend" in policy.get_action(0.20)
    assert "Freeze" in policy.get_action(0.0)
    assert "Freeze" in policy.get_action(-0.1)


@pytest.mark.unit
def test_error_budget_policy_evaluate_severity_levels() -> None:
    """Verify policy evaluate() produces ok, warning, and critical statuses."""
    policy = ErrorBudgetPolicy(slo=AVAILABILITY_SLO)

    ok_status = policy.evaluate(0.995)
    assert ok_status.severity == "ok"
    assert ok_status.is_met is True
    assert ok_status.budget_remaining == pytest.approx(1.0)

    warn_status = policy.evaluate(0.983)  # 70% consumed -> 30% remaining
    assert warn_status.severity == "warning"
    assert warn_status.is_met is False
    assert warn_status.budget_remaining == pytest.approx(0.30)

    crit_status = policy.evaluate(0.97)  # budget exhausted
    assert crit_status.severity == "critical"
    assert crit_status.budget_remaining == pytest.approx(0.0)
    assert "Freeze" in crit_status.recommended_action


@pytest.mark.unit
def test_pvs_metrics_records_requests_latency_cost_tokens_and_quantiles() -> None:
    """Verify PVSMetrics aggregates requests, quantiles, cost, and tokens."""
    metrics = PVSMetrics(enabled=True, register_global_prometheus=False)
    for idx in range(9):
        metrics.record_request(
            agent="discovery",
            status="success",
            latency_seconds=0.5 + idx * 0.25,
            cost_usd=0.05,
            tokens=100,
            model="mock-model",
        )
    metrics.record_request(
        agent="discovery",
        status="error",
        latency_seconds=4.5,
        cost_usd=0.05,
        tokens=50,
        model="mock-model",
        error_type="TimeoutError",
    )

    snap = metrics.get_snapshot()
    assert snap["requests_total"] == 10
    assert snap["requests_success"] == 9
    assert snap["requests_error"] == 1
    assert snap["availability"] == pytest.approx(0.9)
    assert snap["error_rate"] == pytest.approx(0.1)
    assert snap["total_cost_usd"] == pytest.approx(0.50)
    assert snap["total_tokens"] == 950
    assert snap["latency_p50_seconds"] >= 1.5
    assert snap["latency_p95_seconds"] >= 4.5
    assert snap["latency_p99_seconds"] >= snap["latency_p95_seconds"]


@pytest.mark.unit
def test_pvs_metrics_records_evaluations_hallucinations_policy_and_active_runs() -> None:
    """Verify AI-specific and operational gauges/counters in PVSMetrics."""
    metrics = PVSMetrics(enabled=True, register_global_prometheus=False)
    metrics.record_agent_execution(agent="spec", status="success", cost_usd=0.10)
    metrics.record_agent_execution(agent="spec", status="success", cost_usd=0.30)
    metrics.record_evaluation(
        artifact_type="specification",
        score=0.96,
        passed=True,
        hallucination_rate=0.01,
    )
    metrics.record_evaluation(
        artifact_type="specification",
        score=0.60,
        passed=False,
        hallucination_rate=0.03,
    )
    metrics.record_policy_violation(agent="build", decision="DENY")
    metrics.track_active_run(1)
    metrics.track_active_run(1)
    metrics.track_active_run(-1)
    metrics.set_error_budget("availability", 0.75)

    snap = metrics.get_snapshot()
    assert snap["eval_total"] == 2
    assert snap["eval_pass_rate"] == pytest.approx(0.5)
    assert snap["mean_quality_score"] == pytest.approx(0.78)
    assert snap["hallucination_free_rate"] == pytest.approx(0.98)
    assert snap["avg_cost_per_outcome_usd"] == pytest.approx(0.20)
    assert snap["policy_violations"] == 1
    assert snap["safety_compliance"] == pytest.approx(0.5)
    assert snap["active_runs"] == 1
    assert snap["error_budgets"]["availability"] == pytest.approx(0.75)


@pytest.mark.unit
def test_pvs_metrics_disabled_mode_is_noop() -> None:
    """Verify PVSMetrics(enabled=False) safely drops all recordings."""
    metrics = PVSMetrics(enabled=False, register_global_prometheus=False)
    metrics.record_request(agent="discovery", status="success", latency_seconds=1.2, cost_usd=0.5)
    metrics.record_evaluation(artifact_type="outcome", score=0.9, passed=True)
    metrics.record_policy_violation(agent="discovery")
    metrics.track_active_run(1)

    snap = metrics.get_snapshot()
    assert snap["requests_total"] == 0
    assert snap["eval_total"] == 0
    assert snap["policy_violations"] == 0
    assert snap["active_runs"] == 0


@pytest.mark.unit
def test_pvs_metrics_renders_prometheus_exposition_format() -> None:
    """Verify Prometheus text exposition includes required metric families."""
    metrics = PVSMetrics(enabled=True, register_global_prometheus=False)
    metrics.record_request(agent="discovery", status="success", latency_seconds=0.8, cost_usd=0.12)
    metrics.set_error_budget("availability", 0.85)

    body_bytes, content_type = metrics.render_prometheus_metrics()
    text = body_bytes.decode("utf-8")

    assert "text/plain" in content_type
    assert 'pvs_requests_total{status="success"} 1' in text
    assert 'pvs_requests_total{status="error"} 0' in text
    assert "pvs_cost_usd_total 0.12" in text
    assert 'pvs_error_budget_remaining{slo="availability"} 0.85' in text


@pytest.mark.unit
def test_slo_evaluator_multi_window_burn_rate_alerts() -> None:
    """Verify fast (>10x) and slow (>2x) burn-rate alerts require BOTH windows."""
    evaluator = SLOEvaluator(metrics=PVSMetrics(register_global_prometheus=False))

    # 1. Fast burn (>10x of 0.01 = 0.10) on BOTH windows -> both fast and slow fire
    fast_alerts = evaluator.evaluate_burn_rate_alerts(
        AVAILABILITY_SLO,
        short_window_error_rate=0.15,
        long_window_error_rate=0.12,
    )
    by_name = {a.alert_name: a for a in fast_alerts}
    assert by_name["SLOBurnRateFast"].firing is True
    assert by_name["SLOBurnRateFast"].severity == "critical"
    assert by_name["SLOBurnRateSlow"].firing is True

    # 2. Slow burn (>2x = 0.02, <10x = 0.10) on BOTH windows -> only slow fires
    slow_alerts = evaluator.evaluate_burn_rate_alerts(
        AVAILABILITY_SLO,
        short_window_error_rate=0.04,
        long_window_error_rate=0.03,
    )
    by_name = {a.alert_name: a for a in slow_alerts}
    assert by_name["SLOBurnRateFast"].firing is False
    assert by_name["SLOBurnRateSlow"].firing is True
    assert by_name["SLOBurnRateSlow"].severity == "warning"

    # 3. Single-window transient spike must NOT fire multi-window alert (low false positives)
    spike_alerts = evaluator.evaluate_burn_rate_alerts(
        AVAILABILITY_SLO,
        short_window_error_rate=0.25,
        long_window_error_rate=0.005,
    )
    assert all(not a.firing for a in spike_alerts)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_harness_and_evaluation_pipeline_emit_production_metrics() -> None:
    """Verify Harness.invoke_model and EvaluationPipeline.evaluate emit live metrics."""
    reset_metrics()

    ctx = ExecutionContext(run_id="metrics-smoke", agent_name="discovery")
    harness = Harness(context=ctx)
    await harness.invoke_model([{"role": "user", "content": "Draft concise outcome summary."}])

    pipeline = EvaluationPipeline(judges=[_ConstantJudge(score=0.92, passed=True)])
    await pipeline.evaluate(
        {"id": "outcome-001"},
        context={"artifact_type": "outcome_hypothesis"},
    )

    snap = get_metrics().get_snapshot()
    assert snap["requests_success"] >= 1
    assert snap["eval_total"] >= 1
    assert snap["eval_pass_rate"] == pytest.approx(1.0)
    assert snap["mean_quality_score"] == pytest.approx(0.92)


@pytest.mark.unit
def test_monitoring_configs_valid_yaml_json_and_zero_secrets() -> None:
    """Validate all Prometheus, Alertmanager, Grafana, Compose configs and zero secrets."""
    yaml_files = [
        REPO_ROOT / "monitoring" / "prometheus" / "prometheus.yml",
        REPO_ROOT / "monitoring" / "prometheus" / "alerts.yml",
        REPO_ROOT / "monitoring" / "alertmanager" / "config.yml",
        REPO_ROOT / "monitoring" / "grafana" / "datasources.yml",
        REPO_ROOT / "monitoring" / "grafana" / "dashboards" / "dashboards.yml",
        REPO_ROOT / "docker-compose.monitoring.yml",
    ]
    detector = SecretDetector()

    for path in yaml_files:
        assert path.exists(), f"Missing monitoring config: {path}"
        raw = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw)
        assert isinstance(parsed, dict), f"Invalid YAML object in {path}"
        assert detector.find_secrets(raw) == [], f"Secret detected in {path}"

    # Verify Prometheus alert rules (>= 8 rules including burn-rate + AI SLIs)
    alerts_data = yaml.safe_load(
        (REPO_ROOT / "monitoring" / "prometheus" / "alerts.yml").read_text(encoding="utf-8")
    )
    rules = [r for grp in alerts_data["groups"] for r in grp["rules"]]
    rule_names = {r["alert"] for r in rules}
    assert len(rules) >= 8
    assert {
        "SLOBurnRateFast",
        "SLOBurnRateSlow",
        "QualityDegradationCritical",
        "CostPerOutcomeOverrun",
        "PolicyViolationSpike",
    }.issubset(rule_names)

    # Verify all 4 Grafana dashboards parse cleanly with >= 4 panels each
    dashboard_dir = REPO_ROOT / "monitoring" / "grafana" / "dashboards"
    expected_dashboards = ["latency.json", "cost.json", "quality.json", "error_budget.json"]
    for filename in expected_dashboards:
        dash_path = dashboard_dir / filename
        assert dash_path.exists(), f"Missing Grafana dashboard: {dash_path}"
        raw = dash_path.read_text(encoding="utf-8")
        dash = json.loads(raw)
        assert len(dash.get("panels", [])) >= 4
        assert detector.find_secrets(raw) == [], f"Secret detected in {dash_path}"


@pytest.mark.unit
def test_default_slos_catalog_completeness() -> None:
    """Verify DEFAULT_SLOS defines targets for availability, latency, error, quality, cost."""
    assert {
        "availability",
        "latency_p95",
        "error_rate",
        "quality_score",
        "cost_per_outcome",
        "hallucination_free_rate",
        "safety_compliance",
    }.issubset(set(DEFAULT_SLOS.keys()))
    for name, slo in DEFAULT_SLOS.items():
        d = slo.to_dict()
        assert d["name"] == name
        assert d["error_budget"] > 0.0
