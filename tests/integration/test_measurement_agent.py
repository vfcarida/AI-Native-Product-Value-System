"""Integration tests for MeasurementAgent, Learn stage orchestrator integration, schema, and CLI."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.agents.architecture_review_agent import ArchitectureReviewAgent
from product_value_system.agents.discovery_agent import DiscoveryAgent
from product_value_system.agents.security_review_agent import SecurityReviewAgent
from product_value_system.agents.specification_agent import SpecificationAgent
from product_value_system.cli.main import main as cli_main
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.learning import (
    MeasurementAgent,
    MockAnalyticsAdapter,
    OutcomeTracker,
)
from product_value_system.orchestrator.checkpoints import CheckpointManager
from product_value_system.orchestrator.controller import ValueStreamOrchestrator
from product_value_system.orchestrator.state_machine import RunStatus

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_measurement_agent_deterministic_execution() -> None:
    """MeasurementAgent measures a registered outcome without LLM calls and returns AgentResult."""
    adapter = MockAnalyticsAdapter({"Merchant retention rate": 98.4}, ci_half_width_ratio=0.01)
    tracker = OutcomeTracker(adapter)
    tracker.register_deployment(
        outcome_id="outcome-acme-001",
        deployment_date=datetime(2026, 10, 1, tzinfo=UTC),
        outcome_hypothesis={
            "success_criteria": {
                "primary_metric": {"name": "Merchant retention rate"},
                "baseline": 85.0,
                "target": 97.75,
            }
        },
        development_cost_usd=13.4,
    )

    agent = MeasurementAgent(tracker)
    ctx = ExecutionContext(run_id="run-m1", stage_name="measure", agent_name=agent.name)
    harness = Harness(context=ctx)

    result = await agent.execute(
        input_artifact={"outcome_id": "outcome-acme-001", "measurement_window_days": 30},
        harness=harness,
        context=ctx,
    )

    assert result.success is True
    assert result.output_artifact["status"] == "ACHIEVED"
    assert result.output_artifact["primary_metric_name"] == "Merchant retention rate"
    assert result.output_artifact["cost_attribution"]["development_cost_usd"] == 13.4
    assert "llm_insights" not in result.output_artifact
    assert "measured_at" in result.output_artifact


@pytest.mark.asyncio
async def test_measurement_agent_auto_registration() -> None:
    """MeasurementAgent auto-registers outcome hypothesis when supplied inline."""
    adapter = MockAnalyticsAdapter({"checkout_conversion": 39.0}, ci_half_width_ratio=0.01)
    tracker = OutcomeTracker(adapter)
    agent = MeasurementAgent(tracker)
    ctx = ExecutionContext(run_id="run-m2", stage_name="measure", agent_name=agent.name)
    harness = Harness(context=ctx)

    result = await agent.execute(
        input_artifact={
            "id": "outcome-inline",
            "primary_metric": {"name": "checkout_conversion", "baseline": 30.0, "target": 40.0},
            "development_cost_usd": 18.0,
        },
        harness=harness,
        context=ctx,
    )

    assert result.success is True
    assert result.output_artifact["status"] == "PARTIAL_SUCCESS"
    assert "outcome-inline" in tracker.deployments


@pytest.mark.asyncio
async def test_measurement_agent_with_llm_insights() -> None:
    """MeasurementAgent generates qualitative LLM insights when model_adapter is configured."""
    adapter = MockAnalyticsAdapter({"Merchant retention rate": 96.0}, ci_half_width_ratio=0.01)
    tracker = OutcomeTracker(adapter)
    tracker.register_deployment(
        outcome_id="outcome-llm",
        deployment_date=datetime(2026, 10, 1, tzinfo=UTC),
        outcome_hypothesis={
            "primary_metric": {
                "name": "Merchant retention rate",
                "baseline": 85.0,
                "target": 97.75,
            }
        },
        development_cost_usd=22.0,
    )

    mock_llm = MockModelAdapter(
        responses=[
            "1. SMB fee reduction drove strong retention lift.\n"
            "2. Remaining 1.75pp gap concentrated in micro-merchants.\n"
            "3. Next Discovery experiment: automated onboarding checklist."
        ]
    )
    agent = MeasurementAgent(tracker, model_adapter=mock_llm)
    ctx = ExecutionContext(run_id="run-m3", stage_name="measure", agent_name=agent.name)
    harness = Harness(context=ctx, model_adapter=mock_llm)

    result = await agent.execute(
        input_artifact={"outcome_id": "outcome-llm", "measurement_window_days": 30},
        harness=harness,
        context=ctx,
    )

    assert result.success is True
    assert "llm_insights" in result.output_artifact
    assert "SMB fee reduction" in result.output_artifact["llm_insights"]
    assert mock_llm._call_count == 1


@pytest.mark.asyncio
async def test_orchestrator_learn_stage_end_to_end(tmp_path: Path) -> None:
    """ValueStreamOrchestrator runs all 10 stages + Learn stage when enable_measurement=True."""
    validator = SchemaValidator(REPO_ROOT / "schemas")

    outcome_resp = json.dumps(
        {
            "hypothesis": (
                "Reducing payment fees from 3.2% to 2.5% and automating daily "
                "reconciliation will increase merchant retention to 97.75%"
            ),
            "success_criteria": {
                "primary_metric": {
                    "name": "Merchant retention rate",
                    "unit": "percentage",
                    "measurement_method": "Monthly active merchant cohort retention",
                },
                "baseline": 85.0,
                "target": 97.75,
                "guardrails": [
                    {"metric": "Transaction success rate", "threshold": 99.5, "direction": "above"}
                ],
            },
            "evidence_requirements": [
                {
                    "type": "quantitative",
                    "description": "Cohort retention report",
                    "mandatory": True,
                }
            ],
        }
    )
    spec_resp = json.dumps(
        {
            "functional_requirements": [
                {
                    "id": "FR-001",
                    "description": "Apply 2.5% processing fee",
                    "priority": "must-have",
                    "acceptance_criteria": ["Fee equals 2.5%"],
                    "linked_to": ["primary_metric"],
                }
            ],
            "architecture_decisions": [
                {"id": "AD-001", "decision": "Use Decimal", "rationale": "Exact math"}
            ],
            "quality_attributes": {
                "performance": {"latency_ms": 100, "throughput_rps": 500},
                "security": ["Auth required"],
                "scalability": ["100K merchants"],
                "reliability": ["99.9% uptime"],
            },
            "testability_criteria": {
                "unit_tests": ["Fee math"],
                "integration_tests": ["Reconciliation"],
                "acceptance_tests": ["2.5% applied"],
            },
            "constraints": {
                "technical": ["Python 3.12+"],
                "operational": ["Single node"],
                "business": ["Under $50"],
            },
        }
    )
    review_resp = json.dumps(
        {"findings": [], "overall_verdict": "approve", "summary": "Approved"}
    )

    def harness_factory(stage_name: str) -> Harness:
        if stage_name == "outcome_hypothesis":
            return Harness(model_adapter=MockModelAdapter(responses=[outcome_resp]))
        if stage_name == "specification":
            return Harness(model_adapter=MockModelAdapter(responses=[spec_resp]))
        return Harness(model_adapter=MockModelAdapter(responses=[review_resp]))

    analytics = MockAnalyticsAdapter(
        {
            "Merchant retention rate": 98.3,
            "Transaction success rate": 99.9,
        },
        ci_half_width_ratio=0.01,
    )

    orchestrator = ValueStreamOrchestrator(
        agents={
            "discovery": DiscoveryAgent(validator),
            "specification": SpecificationAgent(validator),
            "review_architecture": ArchitectureReviewAgent(validator),
            "review_security": SecurityReviewAgent(validator),
        },
        evaluation_pipeline=None,
        policy_engine=None,
        checkpoint_manager=CheckpointManager(tmp_path / "checkpoints"),
        artifact_root=tmp_path / "artifacts",
        harness_factory=harness_factory,
        analytics_adapter=analytics,
    )

    problem: dict[str, Any] = {
        "id": "problem-learn-e2e",
        "version": 1,
        "created": "2026-11-18T10:00:00Z",
        "statement": (
            "Small merchants struggle with high payment processing fees and "
            "complex manual reconciliation."
        ),
        "context": {
            "user_segment": "Small merchants",
            "business_context": "Increase SMB retention",
        },
        "constraints": {"no_pii": True, "synthetic_only": True},
    }

    run = await orchestrator.execute_run(
        problem,
        config={"enable_measurement": True, "measurement_window_days": 30},
    )

    assert run.status == RunStatus.COMPLETED
    assert len(run.stages) == 11
    assert "measure" in run.stages
    assert run.outcome_achieved is True

    assert run.artifact_dir is not None
    artifact_dir = Path(run.artifact_dir)
    assert (artifact_dir / "measurement.yaml").exists()
    assert (artifact_dir / "discovery_feedback.yaml").exists()

    # Updated outcome.yaml includes measurement and validates cleanly against outcome.schema.json
    with open(artifact_dir / "outcome.yaml", encoding="utf-8") as f:
        enriched_outcome = yaml.safe_load(f)
    assert "measurement" in enriched_outcome
    assert enriched_outcome["measurement"]["status"] == "ACHIEVED"
    validator.validate(enriched_outcome, "outcome.schema.json")

    event_types = [e["type"] for e in orchestrator.events]
    assert "learning.captured" in event_types


@pytest.mark.asyncio
async def test_orchestrator_learn_stage_deferred_measurement(tmp_path: Path) -> None:
    """When defer_measurement=True, orchestrator schedules measurement without analytics queries."""
    validator = SchemaValidator(REPO_ROOT / "schemas")
    outcome_resp = json.dumps(
        {
            "hypothesis": (
                "Automated merchant payouts will lift weekly merchant retention "
                "from 80% to 92% within 30 days"
            ),
            "success_criteria": {
                "primary_metric": {
                    "name": "weekly_retention",
                    "unit": "percentage",
                    "measurement_method": "SQL analytics",
                },
                "baseline": 80.0,
                "target": 92.0,
                "guardrails": [],
            },
            "evidence_requirements": [
                {"type": "quantitative", "description": "Weekly cohort", "mandatory": True}
            ],
        }
    )
    spec_resp = json.dumps(
        {
            "functional_requirements": [
                {
                    "id": "FR-001",
                    "description": "Schedule daily payouts",
                    "priority": "must-have",
                    "acceptance_criteria": ["Payout runs at 00:00 UTC"],
                    "linked_to": ["primary_metric"],
                }
            ],
            "architecture_decisions": [
                {"id": "AD-001", "decision": "Async queue", "rationale": "Resilience"}
            ],
            "quality_attributes": {
                "performance": {"latency_ms": 200, "throughput_rps": 100},
                "security": ["Signed payloads"],
                "scalability": ["10K merchants"],
                "reliability": ["99.9%"],
            },
            "testability_criteria": {
                "unit_tests": ["Scheduler"],
                "integration_tests": ["Payout flow"],
                "acceptance_tests": ["Batch settles"],
            },
            "constraints": {
                "technical": ["Python 3.12+"],
                "operational": ["Single node"],
                "business": ["Budget"],
            },
        }
    )

    def harness_factory(stage_name: str) -> Harness:
        if stage_name == "outcome_hypothesis":
            return Harness(model_adapter=MockModelAdapter(responses=[outcome_resp]))
        return Harness(model_adapter=MockModelAdapter(responses=[spec_resp]))

    orchestrator = ValueStreamOrchestrator(
        agents={
            "discovery": DiscoveryAgent(validator),
            "specification": SpecificationAgent(validator),
        },
        evaluation_pipeline=None,
        policy_engine=None,
        checkpoint_manager=CheckpointManager(tmp_path / "cp"),
        artifact_root=tmp_path / "art",
        harness_factory=harness_factory,
        analytics_adapter=MockAnalyticsAdapter({}),
    )

    problem = {
        "id": "problem-defer-001",
        "version": 1,
        "created": "2026-11-18T10:00:00Z",
        "statement": "Merchants wait too long for settlement payouts and churn during onboarding.",
        "context": {"user_segment": "SMB", "business_context": "Retention"},
        "constraints": {"no_pii": True, "synthetic_only": True},
    }

    run = await orchestrator.execute_run(
        problem,
        config={
            "enable_measurement": True,
            "defer_measurement": True,
            "measurement_window_days": 30,
        },
    )

    assert run.status == RunStatus.COMPLETED
    assert run.artifact_dir is not None
    assert (Path(run.artifact_dir) / "measurement_schedule.yaml").exists()
    assert any(e["type"] == "measurement.scheduled" for e in orchestrator.events)


def test_outcome_schema_validates_example_with_measurement() -> None:
    """examples/learning/outcome_measurement.yaml validates against outcome.schema.json."""
    validator = SchemaValidator(REPO_ROOT / "schemas")
    example_path = REPO_ROOT / "examples" / "learning" / "outcome_measurement.yaml"
    with open(example_path, encoding="utf-8") as f:
        artifact = yaml.safe_load(f)
    validator.validate(artifact, "outcome.schema.json")


def test_cli_measure_outcome_mock_and_sqlite(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """pvs measure-outcome works with both --use-mock-analytics and --db-url SQLite."""
    out_yaml = tmp_path / "measurement_report.yaml"
    exit_code = cli_main(
        [
            "measure-outcome",
            "--outcome-id",
            "outcome-acme-payment",
            "--measurement-window",
            "30",
            "--use-mock-analytics",
            "--current-value",
            "98.5",
            "--dev-cost",
            "15.0",
            "--output",
            str(out_yaml),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "ACHIEVED" in captured.out
    assert out_yaml.exists()

    # Now test with real SQLite analytics DB
    db_path = tmp_path / "cli_analytics.db"
    now = datetime.now(UTC)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE metrics (metric_name TEXT, metric_value REAL, timestamp TEXT)"
        )
        for i in range(1, 10):
            conn.execute(
                "INSERT INTO metrics VALUES (?, ?, ?)",
                ("retention", 82.0 + (i % 2), (now - timedelta(days=35 + i)).isoformat()),
            )
            conn.execute(
                "INSERT INTO metrics VALUES (?, ?, ?)",
                ("retention", 96.0 + (i % 2), (now - timedelta(days=i)).isoformat()),
            )

    exit_code_sql = cli_main(
        [
            "measure-outcome",
            "--outcome-id",
            "outcome-cli-sql",
            "--metric",
            "retention",
            "--baseline",
            "82.0",
            "--target",
            "95.0",
            "--measurement-window",
            "30",
            "--db-url",
            f"sqlite:///{db_path}",
        ]
    )
    captured_sql = capsys.readouterr()
    assert exit_code_sql == 0
    assert "ACHIEVED" in captured_sql.out
