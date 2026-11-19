"""Unit tests for the Learn stage: analytics, OutcomeTracker, CostAttributor, and FeedbackLoop."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from product_value_system.learning import (
    CostAttributor,
    FeedbackAlert,
    FeedbackLoop,
    MockAnalyticsAdapter,
    OutcomeTracker,
    PrometheusAnalyticsAdapter,
    SQLAnalyticsAdapter,
)

pytestmark = pytest.mark.unit


def _init_sqlite_metrics_db(
    db_path: Path,
    rows: list[tuple[str, float, str, str | None, str | None]],
) -> str:
    """Create a test SQLite metrics database and return sqlite:/// connection string."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE metrics (
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                timestamp TEXT NOT NULL,
                dimension_user_segment TEXT,
                dimension_device_type TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO metrics (
                metric_name, metric_value, timestamp, dimension_user_segment, dimension_device_type
            ) VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
    return f"sqlite:///{db_path}"


@pytest.mark.asyncio
async def test_mock_analytics_adapter_metric_and_ci() -> None:
    """MockAnalyticsAdapter returns deterministic value, CI, baseline window, and errors cleanly."""
    adapter = MockAnalyticsAdapter(
        mock_data={"conversion_rate": 42.0},
        baseline_data={"conversion_rate": 35.0},
        sample_size=500,
    )
    now = datetime.now(UTC)

    current = await adapter.query_metric("conversion_rate", now - timedelta(days=30), now)
    assert current.value == 42.0
    assert current.sample_size == 500
    assert current.confidence_interval is not None
    assert current.confidence_interval[0] < 42.0 < current.confidence_interval[1]

    baseline = await adapter.query_metric(
        "conversion_rate",
        now - timedelta(days=60),
        now - timedelta(days=30),
        filters={"_window": "baseline"},
    )
    assert baseline.value == 35.0

    with pytest.raises(ValueError, match="Mock data not found"):
        await adapter.query_metric("missing_metric", now - timedelta(days=7), now)


@pytest.mark.asyncio
async def test_mock_analytics_adapter_timeseries() -> None:
    """MockAnalyticsAdapter generates deterministic daily and hourly timeseries."""
    adapter_a = MockAnalyticsAdapter({"retention": 90.0}, seed=123)
    adapter_b = MockAnalyticsAdapter({"retention": 90.0}, seed=123)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 5, tzinfo=UTC)

    ts_a = await adapter_a.query_timeseries("retention", start, end, granularity="daily")
    ts_b = await adapter_b.query_timeseries("retention", start, end, granularity="daily")

    assert len(ts_a.timestamps) == 5
    assert ts_a.values == ts_b.values
    assert ts_a.granularity == "daily"


@pytest.mark.asyncio
async def test_sql_analytics_adapter_sqlite_aggregate_and_filters(tmp_path: Path) -> None:
    """SQLAnalyticsAdapter computes exact SQLite aggregates, CIs, and dimension filters."""
    base_ts = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    rows = [
        ("retention", 90.0, (base_ts + timedelta(days=0)).isoformat(), "smb", "web"),
        ("retention", 92.0, (base_ts + timedelta(days=1)).isoformat(), "smb", "mobile"),
        ("retention", 94.0, (base_ts + timedelta(days=2)).isoformat(), "smb", "web"),
        ("retention", 70.0, (base_ts + timedelta(days=2)).isoformat(), "enterprise", "web"),
    ]
    conn_str = _init_sqlite_metrics_db(tmp_path / "analytics.db", rows)
    adapter = SQLAnalyticsAdapter(conn_str)

    all_res = await adapter.query_metric(
        "retention",
        base_ts - timedelta(days=1),
        base_ts + timedelta(days=5),
    )
    assert all_res.sample_size == 4
    assert all_res.value == pytest.approx(86.5, abs=0.01)
    assert all_res.confidence_interval is not None

    smb_res = await adapter.query_metric(
        "retention",
        base_ts - timedelta(days=1),
        base_ts + timedelta(days=5),
        filters={"user_segment": "smb"},
    )
    assert smb_res.sample_size == 3
    assert smb_res.value == pytest.approx(92.0, abs=0.01)


@pytest.mark.asyncio
async def test_sql_analytics_adapter_sqlite_timeseries(tmp_path: Path) -> None:
    """SQLAnalyticsAdapter buckets SQLite rows into daily and hourly timeseries."""
    rows = [
        ("latency_ms", 120.0, "2026-10-01T10:00:00+00:00", None, None),
        ("latency_ms", 140.0, "2026-10-01T10:30:00+00:00", None, None),
        ("latency_ms", 100.0, "2026-10-02T09:00:00+00:00", None, None),
    ]
    conn_str = _init_sqlite_metrics_db(tmp_path / "ts.db", rows)
    adapter = SQLAnalyticsAdapter(conn_str)

    daily = await adapter.query_timeseries(
        "latency_ms",
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 10, 3, tzinfo=UTC),
        granularity="daily",
    )
    assert len(daily.timestamps) == 2
    assert daily.values == [130.0, 100.0]


@pytest.mark.asyncio
async def test_sql_analytics_adapter_rejects_sql_injection_filter_key(tmp_path: Path) -> None:
    """SQLAnalyticsAdapter rejects unsafe filter column names to prevent SQL injection."""
    conn_str = _init_sqlite_metrics_db(tmp_path / "sec.db", [])
    adapter = SQLAnalyticsAdapter(conn_str)
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="Invalid filter dimension identifier"):
        await adapter.query_metric(
            "retention",
            now - timedelta(days=1),
            now,
            filters={"segment; DROP TABLE metrics--": "smb"},
        )


@pytest.mark.asyncio
async def test_prometheus_analytics_adapter_query_and_range() -> None:
    """PrometheusAnalyticsAdapter parses Prometheus instant and range query JSON payloads."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/query":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "vector",
                        "result": [
                            {
                                "metric": {"__name__": "checkout_rate"},
                                "value": [1700000000, "0.425"],
                            }
                        ],
                    },
                },
            )
        if request.url.path == "/api/v1/query_range":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [
                            {
                                "metric": {"__name__": "checkout_rate"},
                                "values": [[1700000000, "0.40"], [1700086400, "0.43"]],
                            }
                        ],
                    },
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    adapter = PrometheusAnalyticsAdapter(base_url="http://prom.local", transport=transport)
    try:
        start = datetime(2026, 10, 1, tzinfo=UTC)
        end = datetime(2026, 10, 15, tzinfo=UTC)

        metric = await adapter.query_metric("checkout_rate", start, end)
        assert metric.value == pytest.approx(0.425)
        assert metric.sample_size == 1

        ts = await adapter.query_timeseries("checkout_rate", start, end, granularity="daily")
        assert len(ts.timestamps) == 2
        assert ts.values == [0.40, 0.43]
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_outcome_tracker_status_achieved() -> None:
    """OutcomeTracker reports ACHIEVED when metric significantly meets target."""
    adapter = MockAnalyticsAdapter({"Merchant retention rate": 98.5}, ci_half_width_ratio=0.01)
    tracker = OutcomeTracker(adapter)
    tracker.register_deployment(
        outcome_id="outcome-001",
        deployment_date=datetime(2026, 10, 1, tzinfo=UTC),
        outcome_hypothesis={
            "success_criteria": {
                "primary_metric": {"name": "Merchant retention rate"},
                "baseline": 85.0,
                "target": 97.75,
            }
        },
        development_cost_usd=27.0,
    )

    m = await tracker.measure_outcome("outcome-001", measurement_window_days=30)
    assert m.status == "ACHIEVED"
    assert m.p_value < 0.05
    assert m.cost_attribution["development_cost_usd"] == 27.0
    assert m.cost_attribution["cost_per_unit_improvement"] == pytest.approx(2.0, abs=0.01)


@pytest.mark.asyncio
async def test_outcome_tracker_status_partial_success() -> None:
    """OutcomeTracker reports PARTIAL_SUCCESS for significant improvement short of target."""
    adapter = MockAnalyticsAdapter({"conversion_rate": 38.0}, ci_half_width_ratio=0.01)
    tracker = OutcomeTracker(adapter)
    tracker.register_deployment(
        outcome_id="outcome-partial",
        deployment_date=datetime(2026, 10, 1, tzinfo=UTC),
        outcome_hypothesis={
            "primary_metric": {"name": "conversion_rate", "baseline": 30.0, "target": 45.0}
        },
    )

    m = await tracker.measure_outcome("outcome-partial")
    assert m.status == "PARTIAL_SUCCESS"
    assert m.p_value < 0.05
    assert any("not yet met" in item for item in m.learning)


@pytest.mark.asyncio
async def test_outcome_tracker_status_not_achieved() -> None:
    """OutcomeTracker reports NOT_ACHIEVED when change is statistically insignificant."""
    adapter = MockAnalyticsAdapter({"conversion_rate": 30.2}, ci_half_width_ratio=0.05)
    tracker = OutcomeTracker(adapter)
    tracker.register_deployment(
        outcome_id="outcome-flat",
        deployment_date=datetime(2026, 10, 1, tzinfo=UTC),
        outcome_hypothesis={
            "primary_metric": {"name": "conversion_rate", "baseline": 30.0, "target": 40.0}
        },
    )

    m = await tracker.measure_outcome("outcome-flat")
    assert m.status == "NOT_ACHIEVED"
    assert m.p_value > 0.05


@pytest.mark.asyncio
async def test_outcome_tracker_status_regressed() -> None:
    """OutcomeTracker reports REGRESSED on statistically significant drop below baseline."""
    adapter = MockAnalyticsAdapter({"conversion_rate": 22.0}, ci_half_width_ratio=0.01)
    tracker = OutcomeTracker(adapter)
    tracker.register_deployment(
        outcome_id="outcome-reg",
        deployment_date=datetime(2026, 10, 1, tzinfo=UTC),
        outcome_hypothesis={
            "primary_metric": {"name": "conversion_rate", "baseline": 30.0, "target": 40.0}
        },
    )

    m = await tracker.measure_outcome("outcome-reg")
    assert m.status == "REGRESSED"
    assert m.p_value < 0.05
    assert m.cost_attribution["cost_per_unit_improvement"] is None
    assert any("rollback" in r.lower() for r in m.recommendations)


@pytest.mark.asyncio
async def test_outcome_tracker_lower_is_better_metric() -> None:
    """OutcomeTracker and CostAttributor correctly handle lower-is-better metrics (e.g. latency)."""
    adapter = MockAnalyticsAdapter({"p95_latency_ms": 110.0}, ci_half_width_ratio=0.01)
    tracker = OutcomeTracker(adapter)
    tracker.register_deployment(
        outcome_id="outcome-latency",
        deployment_date=datetime(2026, 10, 1, tzinfo=UTC),
        outcome_hypothesis={
            "primary_metric": {"name": "p95_latency_ms", "baseline": 250.0, "target": 120.0}
        },
        development_cost_usd=140.0,
    )

    m = await tracker.measure_outcome("outcome-latency")
    assert m.status == "ACHIEVED"
    assert m.cost_attribution["improvement"] == pytest.approx(140.0)
    assert m.cost_attribution["cost_per_unit_improvement"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_outcome_tracker_guardrail_breach_detection() -> None:
    """OutcomeTracker flags breached guardrails and recommends halting rollout."""
    adapter = MockAnalyticsAdapter(
        {"retention": 98.0, "error_rate": 2.4},
        ci_half_width_ratio=0.01,
    )
    tracker = OutcomeTracker(adapter)
    tracker.register_deployment(
        outcome_id="outcome-guard",
        deployment_date=datetime(2026, 10, 1, tzinfo=UTC),
        outcome_hypothesis={
            "success_criteria": {
                "primary_metric": {"name": "retention"},
                "baseline": 85.0,
                "target": 95.0,
                "guardrails": [{"metric": "error_rate", "threshold": 1.0, "direction": "below"}],
            }
        },
    )

    m = await tracker.measure_outcome("outcome-guard")
    assert len(m.guardrail_results) == 1
    assert m.guardrail_results[0]["passed"] is False
    assert any("Guardrail breached" in line for line in m.learning)
    assert any("Halt rollout" in rec for rec in m.recommendations)


@pytest.mark.asyncio
async def test_outcome_tracker_sql_pre_and_post_windows(tmp_path: Path) -> None:
    """OutcomeTracker queries pre- and post-deployment metric windows from SQLite."""
    deploy_date = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    rows: list[tuple[str, float, str, str | None, str | None]] = []
    # Pre-deployment baseline (~80.0)
    for i in range(1, 16):
        ts = (deploy_date - timedelta(days=i)).isoformat()
        rows.append(("retention", 80.0 + (i % 3) * 0.5, ts, None, None))
    # Post-deployment improvement (~92.0)
    for i in range(1, 16):
        ts = (deploy_date + timedelta(days=i)).isoformat()
        rows.append(("retention", 92.0 + (i % 3) * 0.5, ts, None, None))

    conn_str = _init_sqlite_metrics_db(tmp_path / "windows.db", rows)
    tracker = OutcomeTracker(SQLAnalyticsAdapter(conn_str))
    tracker.register_deployment(
        outcome_id="outcome-sql",
        deployment_date=deploy_date,
        outcome_hypothesis={
            "primary_metric": {"name": "retention", "baseline": 75.0, "target": 90.0}
        },
        development_cost_usd=24.0,
    )

    m = await tracker.measure_outcome(
        "outcome-sql",
        measurement_window_days=20,
        baseline_window_days=20,
    )
    assert m.baseline_value == pytest.approx(80.5, abs=0.2)
    assert m.current_value == pytest.approx(92.5, abs=0.2)
    assert m.status == "ACHIEVED"


def test_cost_attributor_stage_breakdown_and_zero_improvement() -> None:
    """CostAttributor aggregates multi-stage costs and guards non-positive improvement."""
    attributor = CostAttributor(default_ongoing_monthly_usd=10.0)
    attributor.record_development_cost(
        "outcome-c1",
        5.0,
        stage_costs={"discovery": 2.0, "specification": 3.0},
    )
    attributor.record_development_cost(
        "outcome-c1",
        15.0,
        stage_costs={"build": 15.0},
        ongoing_cost_per_month_usd=30.0,
    )

    gain = attributor.attribute("outcome-c1", baseline_value=50.0, current_value=60.0)
    assert gain.development_cost_usd == 20.0
    assert gain.ongoing_cost_per_month_usd == 30.0
    assert gain.stage_costs == {"discovery": 2.0, "specification": 3.0, "build": 15.0}
    assert gain.cost_per_unit_improvement == pytest.approx(2.0)

    flat = attributor.attribute("outcome-c1", baseline_value=50.0, current_value=50.0)
    assert flat.cost_per_unit_improvement is None


@pytest.mark.asyncio
async def test_feedback_loop_alerts_and_callback() -> None:
    """FeedbackLoop triggers critical alerts on regression/guardrail breaches and runs callbacks."""
    received: list[FeedbackAlert] = []
    adapter = MockAnalyticsAdapter(
        {"conversion": 20.0, "uptime": 97.0},
        ci_half_width_ratio=0.01,
    )
    tracker = OutcomeTracker(adapter)
    tracker.register_deployment(
        outcome_id="outcome-alert",
        deployment_date=datetime(2026, 10, 1, tzinfo=UTC),
        outcome_hypothesis={
            "success_criteria": {
                "primary_metric": {"name": "conversion"},
                "baseline": 30.0,
                "target": 40.0,
                "guardrails": [{"metric": "uptime", "threshold": 99.5, "direction": "above"}],
            }
        },
    )

    loop = FeedbackLoop(tracker, alert_callback=received.append)
    measurement = await tracker.measure_outcome("outcome-alert")
    alerts = await loop.evaluate_measurement(measurement)

    alert_types = {a.alert_type for a in alerts}
    assert alert_types == {"regression", "guardrail_breach"}
    assert all(a.severity == "critical" for a in alerts)
    assert len(received) == 2


@pytest.mark.asyncio
async def test_feedback_loop_cycle_persistence_and_discovery_context(tmp_path: Path) -> None:
    """FeedbackLoop runs measurement cycles, persists YAML history, and builds Discovery context."""
    store = tmp_path / "feedback_history.yaml"
    adapter = MockAnalyticsAdapter(
        {"metric_win": 95.0, "metric_lose": 20.0},
        ci_half_width_ratio=0.01,
    )
    tracker = OutcomeTracker(adapter)
    deploy_date = datetime(2026, 10, 1, tzinfo=UTC)
    tracker.register_deployment(
        outcome_id="outcome-win",
        deployment_date=deploy_date,
        outcome_hypothesis={
            "primary_metric": {"name": "metric_win", "baseline": 80.0, "target": 90.0}
        },
    )
    tracker.register_deployment(
        outcome_id="outcome-lose",
        deployment_date=deploy_date,
        outcome_hypothesis={
            "primary_metric": {"name": "metric_lose", "baseline": 35.0, "target": 45.0}
        },
    )

    loop = FeedbackLoop(tracker, feedback_store_path=store)
    results = await loop.run_measurement_cycle()

    assert set(results.keys()) == {"outcome-win", "outcome-lose"}
    assert store.exists()

    ctx = loop.build_discovery_context(limit=5)
    assert ctx["total_measured_outcomes"] == 2
    assert len(ctx["winning_patterns"]) == 1
    assert len(ctx["anti_patterns"]) == 1
    assert len(ctx["open_recommendations"]) >= 2

    # Reload into fresh FeedbackLoop instance
    reloaded = FeedbackLoop(tracker, feedback_store_path=store)
    assert len(reloaded.feedback_items) == 2
    assert {item.outcome_id for item in reloaded.feedback_items} == {"outcome-win", "outcome-lose"}
