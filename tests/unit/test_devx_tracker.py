"""Unit tests for EVO-032 DevXTracker, CycleTimeTracker, SPACECalculator & SatisfactionSurvey."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from product_value_system.metrics import (
    CycleTimeTracker,
    DevXTracker,
    SatisfactionSurvey,
    SPACECalculator,
    anonymize_developer,
    load_devx_config,
)
from product_value_system.security.hybrid_detector import HybridSecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_start_task_emits_prometheus_metric() -> None:
    reg = CollectorRegistry()
    tracker = DevXTracker({"enabled": True}, registry=reg)

    tracker.start_task("task-001", stage="discovery", agent="discovery-agent")
    tracker.start_task("task-002", stage="discovery", agent="discovery-agent")

    val = reg.get_sample_value(
        "devx_tasks_started_total",
        {"stage": "discovery", "agent": "discovery-agent"},
    )
    assert val == pytest.approx(2.0)


def test_complete_task_computes_cycle_time_correctly() -> None:
    reg = CollectorRegistry()
    tracker = DevXTracker({"enabled": True}, registry=reg)

    t0 = datetime(2026, 11, 22, 10, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=125)
    tracker.start_task("task-10", stage="build", agent="build-agent", timestamp=t0)
    elapsed = tracker.complete_task("task-10", stage="build", success=True, timestamp=t1)

    assert elapsed == pytest.approx(125.0)
    completed = reg.get_sample_value(
        "devx_tasks_completed_total",
        {"stage": "build", "agent": "build-agent", "success": "True"},
    )
    assert completed == pytest.approx(1.0)
    hist_sum = reg.get_sample_value("devx_cycle_time_seconds_sum", {"stage": "build"})
    hist_cnt = reg.get_sample_value("devx_cycle_time_seconds_count", {"stage": "build"})
    assert hist_sum == pytest.approx(125.0)
    assert hist_cnt == pytest.approx(1.0)


def test_record_ai_usage_tracks_acceptance_and_correction_rate() -> None:
    reg = CollectorRegistry()
    tracker = DevXTracker({"enabled": True}, registry=reg)

    tracker.record_ai_usage("build-agent", accepted=True, cost_usd=0.05, time_saved_seconds=600)
    tracker.record_ai_usage("build-agent", accepted=True, cost_usd=0.04, time_saved_seconds=400)
    tracker.record_ai_usage(
        "build-agent",
        accepted=False,
        corrected=True,
        cost_usd=0.06,
        time_saved_seconds=0,
    )
    tracker.record_ai_usage("build-agent", accepted=True, cost_usd=0.05, time_saved_seconds=500)

    eff = tracker.get_ai_effectiveness()
    assert eff["total_ai_invocations"] == pytest.approx(4.0)
    assert eff["acceptance_rate"] == pytest.approx(0.75)
    assert eff["correction_rate"] == pytest.approx(0.25)
    assert eff["avg_cost_per_invocation_usd"] == pytest.approx(0.05)
    assert eff["total_time_saved_seconds"] == pytest.approx(1500.0)

    prom_acc = reg.get_sample_value(
        "devx_ai_usage_total", {"agent": "build-agent", "accepted": "True"}
    )
    prom_rej = reg.get_sample_value(
        "devx_ai_usage_total", {"agent": "build-agent", "accepted": "False"}
    )
    assert prom_acc == pytest.approx(3.0)
    assert prom_rej == pytest.approx(1.0)


async def test_space_calculator_computes_all_five_dimensions() -> None:
    reg = CollectorRegistry()
    tracker = DevXTracker({"enabled": True}, registry=reg)

    # Satisfaction: 4.5 / 5.0 -> 0.90
    tracker.record_satisfaction("tool_satisfaction", 4.5)

    # Activity + Performance + Efficiency: 25 completed fast tasks (200s <= 300s -> eff=1.0)
    base = datetime(2026, 11, 22, 8, 0, 0, tzinfo=UTC)
    for i in range(25):
        tracker.start_task(f"t-{i}", stage="discovery", agent="disc", timestamp=base)
        tracker.complete_task(
            f"t-{i}",
            stage="discovery",
            success=True,
            timestamp=base + timedelta(seconds=200),
        )
        tracker.record_ai_usage("disc", accepted=True)

    # Communication: 30m PR turnaround (<= 1h -> comm=1.0)
    tracker.record_pr_review_turnaround(1800.0)

    calc = SPACECalculator(tracker=tracker, weekly_task_target=50.0)
    scores = await calc.compute_space_scores()

    assert scores["satisfaction"] == pytest.approx(0.9)
    assert scores["performance"] == pytest.approx(1.0)
    assert scores["activity"] == pytest.approx(0.5)
    assert scores["communication"] == pytest.approx(1.0)
    assert scores["efficiency"] == pytest.approx(1.0)
    assert scores["overall"] == pytest.approx((0.9 + 1.0 + 0.5 + 1.0 + 1.0) / 5.0)
    # SPACE gauge emitted to Prometheus registry
    assert reg.get_sample_value("devx_space_score", {"dimension": "overall"}) == pytest.approx(
        scores["overall"]
    )


def test_satisfaction_survey_aggregation_and_pii_scrubbing() -> None:
    survey = SatisfactionSurvey()
    prompt = survey.render_prompt()
    assert "Voluntary & Anonymized" in prompt
    assert "tool_satisfaction" in prompt

    survey.submit_response(
        {
            "tool_satisfaction": 5,
            "cognitive_load": 2,
            "flow_state": 4.0,
            "feedback": "Great tools! Reach me at alice@private-corp.example",
        },
        developer_id="alice@private-corp.example",
    )
    survey.submit_response(
        {
            "tool_satisfaction": 3,
            "cognitive_load": 4,
            "flow_state": 2.0,
            "feedback": "Faster CI builds please.",
        },
        developer_id="bob@private-corp.example",
    )

    with pytest.raises(ValueError, match="between 1 and 5"):
        survey.submit_response({"tool_satisfaction": 9}, developer_id="bad@example.org")

    report = survey.aggregate()
    assert report.response_count == 2
    assert report.rating_averages["tool_satisfaction"] == pytest.approx(4.0)
    assert report.rating_averages["cognitive_load"] == pytest.approx(3.0)
    assert report.numeric_averages["flow_state"] == pytest.approx(3.0)
    assert report.aggregate_only is True

    serialized = str(report.to_dict())
    assert "alice@private-corp.example" not in serialized
    assert "bob@private-corp.example" not in serialized
    assert "[REDACTED_EMAIL]" in serialized


def test_privacy_anonymization_opt_out_and_developer_transparency() -> None:
    reg = CollectorRegistry()
    tracker = DevXTracker({"enabled": True, "anonymize": True}, registry=reg)
    raw_email = "carol.dev@example.org"
    opted_out_email = "dave.dev@example.org"

    tracker.opt_out(opted_out_email)
    t0 = datetime.now(UTC)

    # Opted-out developer events are completely ignored
    tracker.start_task("t-out", stage="build", agent="build", developer_id=opted_out_email)
    tracker.complete_task("t-out", stage="build", success=True, developer_id=opted_out_email)
    tracker.record_ai_usage("build", accepted=True, developer_id=opted_out_email)
    assert len(tracker.task_events) == 0
    assert len(tracker.ai_events) == 0

    # Participating developer ID is hashed everywhere (never stored raw)
    tracker.start_task("t-in", stage="build", agent="build", developer_id=raw_email, timestamp=t0)
    tracker.complete_task(
        "t-in",
        stage="build",
        success=True,
        developer_id=raw_email,
        timestamp=t0 + timedelta(seconds=30),
    )
    tracker.record_ai_usage("build", accepted=True, developer_id=raw_email)

    assert all(e.developer_hash == anonymize_developer(raw_email) for e in tracker.task_events)
    scrape = generate_latest(reg).decode("utf-8")
    assert raw_email not in scrape
    assert opted_out_email not in scrape

    # Developer can inspect their own anonymized summary
    summary = tracker.get_developer_summary(raw_email)
    assert summary["tasks_completed"] == 1
    assert summary["ai_acceptance_rate"] == 1.0
    assert summary["opted_out"] is False
    assert tracker.get_developer_summary(opted_out_email)["opted_out"] is True


def test_event_ordering_and_out_of_order_clamp() -> None:
    ct = CycleTimeTracker()
    t1 = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)
    t0 = t1 - timedelta(seconds=10)

    ct.start_stage("order-1", "spec", timestamp=t1)
    # Earlier completion timestamp is safely clamped to 0.0 instead of negative duration
    timing = ct.complete_stage("order-1", "spec", success=True, timestamp=t0)
    assert timing is not None
    assert timing.duration_seconds == 0.0


def test_missing_start_event_handling() -> None:
    reg = CollectorRegistry()
    tracker = DevXTracker({"enabled": True}, registry=reg)

    # Completing an unknown task logs a warning and returns None without crashing or emitting
    res = tracker.complete_task("never-started-id", stage="review", success=True)
    assert res is None
    assert reg.get_sample_value("devx_cycle_time_seconds_count", {"stage": "review"}) is None


def test_dora_metrics_and_elite_tier_classification() -> None:
    reg = CollectorRegistry()
    tracker = DevXTracker({"enabled": True}, registry=reg)

    base = datetime(2026, 11, 22, 9, 0, 0, tzinfo=UTC)
    for i in range(10):
        tracker.start_task(f"d-{i}", stage="build", agent="build", timestamp=base)
        tracker.complete_task(
            f"d-{i}",
            stage="build",
            success=True,
            timestamp=base + timedelta(seconds=600),
        )
        tracker.record_deployment(success=(i != 9), restore_time_seconds=300.0 if i == 9 else None)

    dora = tracker.get_dora_metrics(window_days=7.0)
    assert dora.deployment_frequency_per_day == pytest.approx(10.0 / 7.0)
    assert dora.lead_time_p50_seconds == pytest.approx(600.0)
    assert dora.change_failure_rate == pytest.approx(0.1)
    assert dora.mttr_seconds == pytest.approx(300.0)
    assert dora.performance_tier == "elite"


def test_devx_disabled_rollback() -> None:
    reg = CollectorRegistry()
    tracker = DevXTracker({"enabled": False}, registry=reg)

    tracker.start_task("t1", stage="discovery", agent="disc")
    assert tracker.complete_task("t1", stage="discovery", success=True) is None
    tracker.record_ai_usage("disc", accepted=True)
    tracker.record_satisfaction("tool_satisfaction", 5.0)
    assert tracker.task_events == []
    assert tracker.ai_events == []


def test_metrics_yaml_config_and_zero_secrets() -> None:
    cfg_path = REPO_ROOT / "config" / "metrics.yaml"
    assert cfg_path.exists()
    cfg = load_devx_config(cfg_path)
    assert cfg["enabled"] is True
    assert cfg["anonymize"] is True
    assert cfg["aggregate_only"] is True
    assert len(cfg["cycle_time"]["stages"]) == 5
    assert len(cfg["surveys"]["questions"]) == 4

    detector = HybridSecretDetector()
    for rel in ("config/metrics.yaml", "docs/devx-metrics.md", "dashboards/devx_dashboard.json"):
        findings = detector.detect((REPO_ROOT / rel).read_text(encoding="utf-8"))
        assert findings == [], f"Unexpected secret findings in {rel}: {findings}"


async def test_empty_tracker_reports_honest_zero_space_and_insufficient_dora() -> None:
    """Empty tracker with zero observations must not fabricate SPACE scores or DORA tiers."""
    tracker = DevXTracker({"enabled": True}, registry=CollectorRegistry())
    scores = await SPACECalculator(tracker=tracker).compute_space_scores()
    assert scores == {
        "satisfaction": 0.0,
        "performance": 0.0,
        "activity": 0.0,
        "communication": 0.0,
        "efficiency": 0.0,
        "overall": 0.0,
    }
    assert tracker.get_dora_metrics().performance_tier == "insufficient_data"

    # Partially observed tracker averages overall strictly over observed dimensions
    tracker.record_satisfaction("tool_satisfaction", 4.0)
    partial = await SPACECalculator(tracker=tracker).compute_space_scores()
    assert partial["satisfaction"] == pytest.approx(0.8)
    assert partial["overall"] == pytest.approx(0.8)
