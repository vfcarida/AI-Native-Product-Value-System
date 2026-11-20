"""
Integration tests for EVO-014: Health, Readiness, Metrics, SLO APIs & Automated Rollback.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from product_value_system.api.health import create_app
from product_value_system.monitoring.metrics import PVSMetrics
from product_value_system.monitoring.rollback import ProductionMonitor, RollbackEvent
from product_value_system.monitoring.slo import AVAILABILITY_SLO, QUALITY_SLO
from product_value_system.monitoring.slo_evaluator import AlertRuleEvaluation, SLOEvaluator


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_endpoint_returns_healthy() -> None:
    """GET /health returns 200 OK with service status and version."""
    metrics = PVSMetrics(register_global_prometheus=False)
    app = create_app(metrics=metrics)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert "version" in payload


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ready_endpoint_checks_dependencies_ok_and_degraded_503() -> None:
    """GET /ready returns 200 when healthy and 503 when any dependency check fails."""
    metrics = PVSMetrics(register_global_prometheus=False)
    healthy_app = create_app(
        metrics=metrics,
        db_check=lambda: True,
        sandbox_check=lambda: {"status": "ok", "engine": "docker"},
        llm_check=lambda: True,
    )

    async with AsyncClient(
        transport=ASGITransport(app=healthy_app),
        base_url="http://test",
    ) as client:
        ok_resp = await client.get("/ready")

    assert ok_resp.status_code == 200
    ok_data = ok_resp.json()
    assert ok_data["status"] == "ready"
    assert ok_data["checks"]["database"]["status"] == "ok"
    assert ok_data["checks"]["sandbox"]["status"] == "ok"
    assert ok_data["checks"]["llm"]["status"] == "ok"

    def _failing_db() -> bool:
        raise RuntimeError("database connection refused")

    degraded_app = create_app(
        metrics=metrics,
        db_check=_failing_db,
        sandbox_check=lambda: False,
        llm_check=lambda: True,
    )

    async with AsyncClient(
        transport=ASGITransport(app=degraded_app),
        base_url="http://test",
    ) as client:
        fail_resp = await client.get("/ready")

    assert fail_resp.status_code == 503
    fail_data = fail_resp.json()
    assert fail_data["status"] == "not_ready"
    assert fail_data["checks"]["database"]["status"] == "error"
    assert "refused" in fail_data["checks"]["database"]["message"]
    assert fail_data["checks"]["sandbox"]["status"] == "error"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_prometheus_scrape_metrics_endpoint() -> None:
    """GET /metrics exposes recorded request, latency, and cost metrics."""
    metrics = PVSMetrics(register_global_prometheus=False)
    metrics.record_request(
        agent="discovery",
        status="success",
        latency_seconds=1.25,
        cost_usd=0.08,
        tokens=320,
    )
    app = create_app(metrics=metrics, metrics_api_key="")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert "pvs_requests_total" in response.text
    assert 'status="success"' in response.text
    assert "pvs_cost_usd_total 0.08" in response.text
    assert "pvs_tokens_total 320" in response.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_metrics_endpoint_enforces_optional_api_key() -> None:
    """GET /metrics enforces X-API-Key or Bearer token when metrics_api_key is set."""
    metrics = PVSMetrics(register_global_prometheus=False)
    app = create_app(metrics=metrics, metrics_api_key="scrape-guard-token")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unauth = await client.get("/metrics")
        wrong = await client.get("/metrics", headers={"X-API-Key": "wrong"})
        via_header = await client.get("/metrics", headers={"X-API-Key": "scrape-guard-token"})
        via_bearer = await client.get(
            "/metrics",
            headers={"Authorization": "Bearer scrape-guard-token"},
        )

    assert unauth.status_code == 401
    assert wrong.status_code == 401
    assert via_header.status_code == 200
    assert via_bearer.status_code == 200


@pytest.mark.integration
@pytest.mark.asyncio
async def test_slos_endpoint_returns_live_compliance_and_budgets() -> None:
    """GET /slos returns evaluated compliance and remaining budgets for all SLOs."""
    metrics = PVSMetrics(register_global_prometheus=False)
    for _ in range(99):
        metrics.record_request(agent="discovery", status="success", latency_seconds=0.6)
    metrics.record_request(agent="discovery", status="error", latency_seconds=1.1)

    app = create_app(metrics=metrics)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/slos")

    assert response.status_code == 200
    payload = response.json()
    assert "slos" in payload
    assert "metrics_snapshot" in payload
    avail = payload["slos"]["availability"]
    assert avail["actual"] == pytest.approx(0.99)
    assert avail["is_met"] is True
    assert avail["budget_remaining"] == pytest.approx(1.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_slo_evaluator_computes_compliance_for_availability_and_quality() -> None:
    """SLOEvaluator computes live compliance from PVSMetrics across SLIs."""
    metrics = PVSMetrics(register_global_prometheus=False)
    for _ in range(99):
        metrics.record_request(agent="discovery", status="success")
    metrics.record_request(agent="discovery", status="error")

    for _ in range(19):
        metrics.record_evaluation(artifact_type="spec", score=0.96, passed=True)
    metrics.record_evaluation(artifact_type="spec", score=0.55, passed=False)

    evaluator = SLOEvaluator(metrics=metrics)
    availability = await evaluator.compute_compliance(AVAILABILITY_SLO)
    quality = await evaluator.compute_compliance(QUALITY_SLO)

    assert availability == pytest.approx(0.99)
    assert AVAILABILITY_SLO.is_met(availability)
    assert quality == pytest.approx(0.95)
    assert QUALITY_SLO.is_met(quality)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_automated_rollback_triggers_on_error_budget_exhaustion() -> None:
    """ProductionMonitor triggers automated rollback & kill switch when budget hits 0."""
    metrics = PVSMetrics(register_global_prometheus=False)
    captured_events: list[RollbackEvent] = []
    kill_switch_calls: list[bool] = []

    monitor = ProductionMonitor(
        slos=[AVAILABILITY_SLO],
        metrics=metrics,
        rollback_enabled=True,
        current_version="0.2.0",
        previous_version="0.1.0",
        rollback_callback=lambda ev: captured_events.append(ev),
        kill_switch_callback=lambda: kill_switch_calls.append(True),
    )

    # Record 90 successes and 10 errors -> 0.90 availability (far below 0.99 target / 0.01 budget)
    for _ in range(90):
        metrics.record_request(agent="discovery", status="success")
    for _ in range(10):
        metrics.record_request(agent="discovery", status="error", error_type="ModelError")

    statuses = await monitor.evaluate_slos()

    assert statuses["availability"].budget_remaining == pytest.approx(0.0)
    assert monitor.rollback_triggered is True
    assert monitor.current_version == "0.1.0"
    assert monitor.rollback_reason is not None
    assert "availability" in monitor.rollback_reason
    assert len(captured_events) == 1
    assert captured_events[0].triggered_by == "error_budget_exhausted"
    assert captured_events[0].rolled_back_from_version == "0.2.0"
    assert len(kill_switch_calls) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_automated_rollback_triggers_on_critical_burn_rate_alert() -> None:
    """ProductionMonitor triggers rollback immediately when a critical alert fires."""
    metrics = PVSMetrics(register_global_prometheus=False)
    metrics.record_request(agent="discovery", status="success")

    monitor = ProductionMonitor(
        slos=[AVAILABILITY_SLO],
        metrics=metrics,
        rollback_enabled=True,
        current_version="0.2.1",
        previous_version="0.2.0",
    )

    critical_alert = AlertRuleEvaluation(
        alert_name="SLOBurnRateFast",
        severity="critical",
        firing=True,
        burn_rate=12.5,
        value=0.125,
        threshold=0.10,
        summary="Fast SLO burn rate on 'availability' (12.5x budget)",
        runbook_url="docs/runbooks/alerting.md#sloburnratefast",
    )

    await monitor.evaluate_slos(alerts=[critical_alert])

    assert monitor.rollback_triggered is True
    assert monitor.current_version == "0.2.0"
    assert len(monitor.rollback_events) == 1
    assert monitor.rollback_events[0].triggered_by == "critical_alert"
    assert monitor.rollback_events[0].slo_name == "SLOBurnRateFast"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_production_monitor_does_not_rollback_when_disabled() -> None:
    """Verify rollback_enabled=False records degraded SLO without triggering rollback."""
    metrics = PVSMetrics(register_global_prometheus=False)
    for _ in range(10):
        metrics.record_request(agent="discovery", status="error")

    monitor = ProductionMonitor(
        slos=[AVAILABILITY_SLO],
        metrics=metrics,
        rollback_enabled=False,
        current_version="0.2.0",
        previous_version="0.1.0",
    )

    statuses = await monitor.evaluate_slos()
    assert statuses["availability"].budget_remaining == pytest.approx(0.0)
    assert monitor.rollback_triggered is False
    assert monitor.current_version == "0.2.0"
