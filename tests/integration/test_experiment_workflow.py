"""
Integration tests for EVO-016: Experiment Design Agent, Full A/B Lifecycle,
Orchestrator Integration, Outcome Measurement Linkage, and CLI.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from product_value_system.agents.experiment_design_agent import ExperimentDesignAgent
from product_value_system.cli.main import main as cli_main
from product_value_system.experiment.ab_test import ABTest
from product_value_system.experiment.models import (
    ExperimentDesign,
    ExperimentStatus,
    GuardrailMetric,
    StatisticalTestType,
    Variant,
)
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.learning.analytics_adapter import MockAnalyticsAdapter
from product_value_system.learning.outcome_tracker import OutcomeTracker
from product_value_system.orchestrator.checkpoints import CheckpointManager
from product_value_system.orchestrator.controller import ValueStreamOrchestrator


def _sample_payment_outcome() -> dict[str, Any]:
    return {
        "id": "outcome-payment-flow-001",
        "version": 1,
        "created": "2026-11-20T10:00:00Z",
        "problem_id": "problem-payment-flow-001",
        "hypothesis": (
            "Streamlining merchant onboarding and checkout retries will increase "
            "completed payment conversion rate from 45% to 60% without harming latency."
        ),
        "success_criteria": {
            "primary_metric": {
                "name": "payment_conversion_rate",
                "unit": "ratio",
                "measurement_method": "production_checkout_telemetry",
            },
            "baseline": 0.45,
            "target": 0.60,
            "guardrails": [
                {"metric": "checkout_error_rate", "threshold": 0.01, "direction": "above"},
                {"metric": "revenue_per_merchant", "threshold": 0.05, "direction": "below"},
            ],
        },
        "daily_traffic": 2000,
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_experiment_design_agent_with_real_outcome_schema() -> None:
    """ExperimentDesignAgent generates a valid design from an outcome.schema.json artifact."""
    outcome = _sample_payment_outcome()
    ctx = ExecutionContext(run_id="int-exp-01", agent_name="experiment-design-agent")
    harness = Harness(context=ctx)
    agent = ExperimentDesignAgent()

    result = await agent.execute(outcome, harness, ctx)

    assert result.success is True
    design = ExperimentDesign.from_dict(result.output_artifact)
    assert design.outcome_id == "outcome-payment-flow-001"
    assert design.sample_size_per_variant > 0
    assert 7 <= design.duration_days <= 30
    assert len(design.guardrails) >= 2
    assert design.hypothesis.primary_metric == "payment_conversion_rate"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ab_test_full_lifecycle_and_state_persistence(tmp_path: Path) -> None:
    """Full A/B lifecycle: design -> start -> assign -> record -> guardrails -> save/load."""
    outcome = _sample_payment_outcome()
    ctx = ExecutionContext(run_id="int-exp-02", agent_name="experiment-design-agent")
    agent = ExperimentDesignAgent()
    res = await agent.execute(outcome, Harness(context=ctx), ctx)
    design = ExperimentDesign.from_dict(res.output_artifact)
    design.id = "experiment-lifecycle-fixed"

    ab_test = ABTest(design)
    ab_test.start()

    rng = np.random.default_rng(42)
    for idx in range(1000):
        uid = f"merchant-{idx}"
        variant = ab_test.assign_variant(uid)
        prob = 0.68 if variant == Variant.TREATMENT else 0.45
        converted = 1.0 if rng.random() < prob else 0.0
        ab_test.record_metric(uid, converted)
        ab_test.record_guardrail(
            "revenue_per_merchant",
            uid,
            102.0 if variant == Variant.TREATMENT else 100.0,
        )
        ab_test.record_guardrail(
            "checkout_error_rate",
            uid,
            0.005,
        )

    ab_test.complete()
    state_file = tmp_path / "experiment_state.json"
    ab_test.save_state(state_file)

    reloaded = ABTest.load_state(state_file)
    result = reloaded.analyze(test_type=StatisticalTestType.CHI_SQUARE)

    assert reloaded.status == ExperimentStatus.COMPLETED
    assert result.statistically_significant is True
    assert result.practically_significant is True
    assert result.recommendation == "adopt"
    assert result.completed_at is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_experiment_results_connect_to_outcome_tracker_validation() -> None:
    """Adopted A/B test treatment metrics align cleanly with EVO-010 OutcomeTracker."""
    design = ExperimentDesign(
        id="experiment-learn-link",
        outcome_id="outcome-payment-flow-001",
        hypothesis=ExperimentDesignAgent()._build_hypothesis(_sample_payment_outcome()),
        guardrails=[
            GuardrailMetric(name="checkout_error_rate", threshold=0.02, direction="increase")
        ],
    )
    ab_test = ABTest(design)
    ab_test.start()
    for i in range(600):
        uid = f"u-{i}"
        v = ab_test.assign_variant(uid)
        ab_test.record_metric(uid, 0.62 if v == Variant.TREATMENT else 0.45)

    exp_res = ab_test.analyze()
    assert exp_res.recommendation == "adopt"

    # Validate achieved post-launch lift in EVO-010 OutcomeTracker
    outcome = _sample_payment_outcome()
    analytics = MockAnalyticsAdapter(
        mock_data={
            "payment_conversion_rate": exp_res.treatment_mean,
            "checkout_error_rate": 0.005,
            "revenue_per_merchant": 100.0,
        },
        baseline_data={"payment_conversion_rate": exp_res.control_mean},
    )
    tracker = OutcomeTracker(analytics)
    tracker.register_deployment(
        outcome_id="outcome-payment-flow-001",
        deployment_date=datetime.now(UTC) - timedelta(days=30),
        outcome_hypothesis=outcome,
        development_cost_usd=15.0,
    )
    measurement = await tracker.measure_outcome("outcome-payment-flow-001")
    assert measurement.status == "ACHIEVED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_orchestrator_runs_experiment_design_when_enabled(tmp_path: Path) -> None:
    """ValueStreamOrchestrator runs ExperimentDesignAgent and emits experiment.designed event."""
    outcome = _sample_payment_outcome()

    class _StubAgent:
        def __init__(self, name: str, payload: dict[str, Any]) -> None:
            self.name = name
            self._payload = payload

        async def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return dict(self._payload)

    orchestrator = ValueStreamOrchestrator(
        agents={
            "discovery": _StubAgent("discovery", outcome),
            "specification": _StubAgent("specification", {"id": "spec-1", "title": "Spec"}),
            "build": _StubAgent("build", {"id": "build-1", "status": "ok"}),
        },
        evaluation_pipeline=None,
        policy_engine=None,
        checkpoint_manager=CheckpointManager(tmp_path / "checkpoints"),
        artifact_root=tmp_path / "artifacts",
    )

    run = await orchestrator.execute_run(
        problem={"id": "problem-payment-flow-001", "title": "Payment conversion"},
        config={"enable_experiment_design": True},
    )

    outcome_stage = run.get_stage("outcome_hypothesis")
    assert outcome_stage is not None
    design_path = Path(outcome_stage.metadata["experiment_design_path"])
    assert design_path.exists()

    saved_design = yaml.safe_load(design_path.read_text(encoding="utf-8"))
    assert saved_design["sample_size_per_variant"] > 0
    assert any(ev["type"] == "experiment.designed" for ev in orchestrator.events)


@pytest.mark.integration
def test_cli_experiment_sample_size_design_and_analyze(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI `pvs experiment` supports sample-size, design, and analyze commands."""
    # 1. pvs experiment sample-size
    assert (
        cli_main(
            [
                "experiment",
                "sample-size",
                "--baseline",
                "0.45",
                "--mde",
                "0.10",
                "--metric-type",
                "proportion",
            ]
        )
        == 0
    )
    out_size = capsys.readouterr().out
    assert "Sample size per variant:" in out_size

    # 2. pvs experiment design
    outcome_file = tmp_path / "outcome.yaml"
    outcome_file.write_text(yaml.safe_dump(_sample_payment_outcome()), encoding="utf-8")
    design_file = tmp_path / "experiment_design.yaml"

    assert (
        cli_main(
            [
                "experiment",
                "design",
                str(outcome_file),
                "--output",
                str(design_file),
                "--daily-traffic",
                "2000",
            ]
        )
        == 0
    )
    assert design_file.exists()
    out_design = capsys.readouterr().out
    assert "Experiment Design:" in out_design

    # 3. pvs experiment analyze (Bayesian & t-test)
    design = ExperimentDesign.from_dict(yaml.safe_load(design_file.read_text(encoding="utf-8")))
    ab_test = ABTest(design)
    ab_test.start()
    ab_test.data["control"] = [1.0] * 180 + [0.0] * 220
    ab_test.data["treatment"] = [1.0] * 260 + [0.0] * 140
    ab_test.complete()

    state_file = tmp_path / "exp_state.json"
    ab_test.save_state(state_file)
    report_json = tmp_path / "report.json"

    assert (
        cli_main(
            [
                "experiment",
                "analyze",
                str(state_file),
                "--test-type",
                "bayesian",
                "--output",
                str(report_json),
            ]
        )
        == 0
    )
    out_analyze = capsys.readouterr().out
    assert "ADOPT" in out_analyze
    assert report_json.exists()
    report_data = json.loads(report_json.read_text(encoding="utf-8"))
    assert report_data["recommendation"] == "adopt"
    assert report_data["bayesian_prob_treatment_beats_control"] > 0.99
