"""Integration tests for EVO-013: parallel 4-agent review, orchestrator, and pvs review CLI."""

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.adapters.model_adapter import ModelResponse, Usage
from product_value_system.agents.base_review_agent import BaseReviewAgent
from product_value_system.agents.discovery_agent import DiscoveryAgent
from product_value_system.agents.specification_agent import SpecificationAgent
from product_value_system.cli.main import main as cli_main
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.orchestrator.checkpoints import CheckpointManager
from product_value_system.orchestrator.controller import (
    OrchestratorError,
    ValueStreamOrchestrator,
)
from product_value_system.orchestrator.state_machine import RunStatus
from product_value_system.review import (
    FindingCategory,
    MultiAgentReviewOrchestrator,
    execute_multi_agent_review,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def validator() -> SchemaValidator:
    return SchemaValidator(REPO_ROOT / "schemas")


@pytest.fixture
def payment_spec() -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "id": "spec-payment-multi-001",
        "version": 1,
        "created": now,
        "outcome_id": "outcome-payment-multi-001",
        "functional_requirements": [
            {
                "id": "FR-001",
                "description": "Send encrypted push notification within 3 seconds of settlement",
                "priority": "must-have",
                "acceptance_criteria": ["Push delivered in <3s", "Payload encrypted"],
                "linked_to": ["primary_metric"],
            }
        ],
        "architecture_decisions": [
            {
                "id": "AD-001",
                "decision": "Transactional outbox with async worker pool",
                "rationale": "Guarantees at-least-once delivery without blocking ledger commit",
            }
        ],
        "quality_attributes": {
            "performance": {"latency_ms": 200, "throughput_rps": 1000},
            "security": ["TLS 1.3", "JWT auth", "Encrypted payload"],
            "scalability": ["Auto-scaling consumer workers"],
            "reliability": ["99.95% availability"],
        },
        "testability_criteria": {
            "unit_tests": ["Test encryption helper"],
            "integration_tests": ["Test outbox dispatch"],
            "acceptance_tests": ["Verify <3s delivery"],
        },
        "constraints": {
            "technical": ["Python 3.12+"],
            "operational": ["Docker"],
            "business": ["Budget <= $50K"],
        },
    }


class _DelayedMockAdapter(MockModelAdapter):
    """Async adapter adding synthetic latency to verify true asyncio.gather concurrency."""

    def __init__(self, response: str, delay_seconds: float = 0.15) -> None:
        super().__init__(responses=[response])
        self._delay = delay_seconds

    async def invoke(self, messages: Any, **kwargs: Any) -> ModelResponse:
        await asyncio.sleep(self._delay)
        return ModelResponse(
            content=self._responses[0],
            model="mock-delayed",
            usage=Usage(input_tokens=100, output_tokens=50, total_tokens=150),
            finish_reason="stop",
        )


@pytest.mark.asyncio
async def test_multi_agent_review_runs_all_4_perspectives_in_parallel(
    validator: SchemaValidator,
    payment_spec: dict[str, Any],
) -> None:
    """All 4 specialized reviewers execute concurrently via asyncio.gather in <0.5s."""

    def factory(reviewer_key: str) -> Harness:
        payload = json.dumps(
            [
                {
                    "severity": "MEDIUM",
                    "category": reviewer_key,
                    "description": f"{reviewer_key.capitalize()} observation on FR-001",
                    "recommendation": f"Apply {reviewer_key} best practice",
                    "requirement_id": "FR-001",
                }
            ]
        )
        return Harness(model_adapter=_DelayedMockAdapter(payload, delay_seconds=0.15))

    start = time.perf_counter()
    result = await execute_multi_agent_review(
        payment_spec,
        validator,
        harness_factory=factory,
        context=ExecutionContext(run_id="int-parallel-001"),
    )
    elapsed = time.perf_counter() - start

    # 4 * 0.15s sequential would be >= 0.60s; parallel completes near 0.15s
    assert elapsed < 0.55
    assert result.gate_decision == "PASS"
    assert len(result.findings) == 4
    reviewers = {f.reviewer for f in result.findings}
    assert reviewers == {
        "security-review-agent",
        "performance-review-agent",
        "maintainability-review-agent",
        "architecture-review-agent",
    }


@pytest.mark.asyncio
async def test_multi_agent_review_supports_custom_reviewer_plugin(
    validator: SchemaValidator,
    payment_spec: dict[str, Any],
) -> None:
    """Custom BaseReviewAgent subclasses plug seamlessly into MultiAgentReviewOrchestrator."""

    class _AccessibilityReviewAgent(BaseReviewAgent):
        review_type = "accessibility"
        evidence_type = "review"
        default_category = FindingCategory.MAINTAINABILITY

        def __init__(self, sv: SchemaValidator) -> None:
            super().__init__(sv, name="accessibility-review-agent")

        def _build_system_prompt(self) -> str:
            return "Check WCAG 2.2 AA compliance."

        def _build_review_prompt(self, spec: dict[str, Any]) -> str:
            return f"Review {spec['id']} for accessibility."

    def factory(reviewer_key: str) -> Harness:
        if reviewer_key == "accessibility":
            body = json.dumps(
                [
                    {
                        "severity": "LOW",
                        "category": "maintainability",
                        "description": (
                            "Ensure push notifications provide screen-reader accessible labels"
                        ),
                        "recommendation": "Include aria-label and concise summary text",
                        "requirement_id": "FR-001",
                    }
                ]
            )
        else:
            body = json.dumps([])
        return Harness(model_adapter=MockModelAdapter(responses=[body]))

    orch = MultiAgentReviewOrchestrator(
        schema_validator=validator,
        harness_factory=factory,
        extra_reviewers=[_AccessibilityReviewAgent(validator)],
    )
    result = await orch.execute(payment_spec)
    assert "accessibility-review-agent" in result.reviewers_executed
    assert len(result.reviewers_executed) == 5
    assert any(f.reviewer == "accessibility-review-agent" for f in result.findings)


@pytest.mark.asyncio
async def test_orchestrator_multi_agent_review_stage_passes_and_saves_artifact(
    validator: SchemaValidator,
    tmp_path: Path,
) -> None:
    """ValueStreamOrchestrator runs parallel 4-agent review and saves consolidated_review.yaml."""
    outcome_resp = json.dumps(
        {
            "hypothesis": (
                "Delivering encrypted push alerts within 3 seconds will raise "
                "notification delivery rate from 60% to 95%"
            ),
            "success_criteria": {
                "primary_metric": {
                    "name": "notification_delivery_rate",
                    "unit": "percentage",
                    "measurement_method": "Server delivery telemetry",
                },
                "baseline": 60.0,
                "target": 95.0,
                "guardrails": [
                    {"metric": "p95_latency_ms", "threshold": 3000.0, "direction": "below"}
                ],
            },
            "evidence_requirements": [
                {"type": "quantitative", "description": "Delivery logs", "mandatory": True}
            ],
        }
    )
    spec_resp = json.dumps(
        {
            "functional_requirements": [
                {
                    "id": "FR-001",
                    "description": "Dispatch encrypted push notification",
                    "priority": "must-have",
                    "acceptance_criteria": ["Delivered in <3s"],
                    "linked_to": ["primary_metric"],
                }
            ],
            "architecture_decisions": [
                {"id": "AD-001", "decision": "Async outbox", "rationale": "Resilience"}
            ],
            "quality_attributes": {
                "performance": {"latency_ms": 200},
                "security": ["TLS 1.3", "JWT"],
                "scalability": ["Horizontal"],
                "reliability": ["99.9%"],
            },
            "testability_criteria": {
                "unit_tests": ["Parser"],
                "integration_tests": ["Dispatch"],
                "acceptance_tests": ["<3s"],
            },
            "constraints": {
                "technical": ["Python 3.12+"],
                "operational": ["Docker"],
                "business": ["Budget"],
            },
        }
    )
    clean_review = json.dumps({"findings": [], "overall_verdict": "approve", "summary": "Approved"})

    def harness_factory(stage_or_key: str) -> Harness:
        if stage_or_key == "outcome_hypothesis":
            return Harness(model_adapter=MockModelAdapter(responses=[outcome_resp]))
        if stage_or_key == "specification":
            return Harness(model_adapter=MockModelAdapter(responses=[spec_resp]))
        return Harness(model_adapter=MockModelAdapter(responses=[clean_review]))

    orchestrator = ValueStreamOrchestrator(
        agents={
            "discovery": DiscoveryAgent(validator),
            "specification": SpecificationAgent(validator),
        },
        evaluation_pipeline=None,
        policy_engine=None,
        checkpoint_manager=CheckpointManager(tmp_path / "cp"),
        artifact_root=tmp_path / "artifacts",
        harness_factory=harness_factory,
        use_multi_agent_review=True,
    )

    problem = {
        "id": "problem-multi-rev-pass",
        "version": 1,
        "created": "2026-11-19T10:00:00Z",
        "statement": "Users lack timely payment completion notifications and call support.",
        "context": {"user_segment": "Mobile banking users", "business_context": "Reduce tickets"},
        "constraints": {"no_pii": True, "synthetic_only": True},
    }

    run = await orchestrator.execute_run(problem)
    assert run.status == RunStatus.COMPLETED
    assert len(run.stages) == 10
    assert run.artifact_dir is not None
    consolidated_file = Path(run.artifact_dir) / "consolidated_review.yaml"
    assert consolidated_file.exists()
    data = yaml.safe_load(consolidated_file.read_text(encoding="utf-8"))
    assert data["gate_decision"] == "PASS"
    assert len(data["reviewers_executed"]) == 4
    assert any(e["type"] == "review.synthesized" for e in orchestrator.events)


@pytest.mark.asyncio
async def test_orchestrator_multi_agent_review_blocks_on_critical_finding(
    validator: SchemaValidator,
    tmp_path: Path,
) -> None:
    """Multi-agent review gate blocks value stream progression when CRITICAL finding occurs."""
    outcome_resp = json.dumps(
        {
            "hypothesis": (
                "Enabling self-service billing exports will lift retention from 80% to 92% "
                "over 30 days"
            ),
            "success_criteria": {
                "primary_metric": {
                    "name": "retention",
                    "unit": "percentage",
                    "measurement_method": "SQL",
                },
                "baseline": 80.0,
                "target": 92.0,
                "guardrails": [],
            },
            "evidence_requirements": [
                {"type": "quantitative", "description": "Cohort query", "mandatory": True}
            ],
        }
    )
    spec_resp = json.dumps(
        {
            "functional_requirements": [
                {
                    "id": "FR-001",
                    "description": "Export billing records",
                    "priority": "must-have",
                    "acceptance_criteria": ["Exports CSV"],
                    "linked_to": ["primary_metric"],
                }
            ],
            "architecture_decisions": [
                {"id": "AD-001", "decision": "Direct export", "rationale": "Simple"}
            ],
            "quality_attributes": {"security": ["None"]},
        }
    )
    critical_sec = json.dumps(
        [
            {
                "severity": "CRITICAL",
                "category": "security",
                "description": "No authentication on billing export endpoint",
                "recommendation": "Require OAuth 2.0 + tenant isolation",
                "requirement_id": "FR-001",
            }
        ]
    )
    clean_other = json.dumps([])

    def harness_factory(stage_or_key: str) -> Harness:
        if stage_or_key == "outcome_hypothesis":
            return Harness(model_adapter=MockModelAdapter(responses=[outcome_resp]))
        if stage_or_key == "specification":
            return Harness(model_adapter=MockModelAdapter(responses=[spec_resp]))
        if stage_or_key == "security":
            return Harness(model_adapter=MockModelAdapter(responses=[critical_sec]))
        return Harness(model_adapter=MockModelAdapter(responses=[clean_other]))

    orchestrator = ValueStreamOrchestrator(
        agents={
            "discovery": DiscoveryAgent(validator),
            "specification": SpecificationAgent(validator),
        },
        evaluation_pipeline=None,
        policy_engine=None,
        checkpoint_manager=CheckpointManager(tmp_path / "cp"),
        artifact_root=tmp_path / "artifacts",
        harness_factory=harness_factory,
    )
    problem = {
        "id": "problem-multi-rev-block",
        "version": 1,
        "created": "2026-11-19T10:00:00Z",
        "statement": "Finance users need self-service billing CSV exports for monthly close.",
        "context": {"user_segment": "Finance teams", "business_context": "Retention"},
        "constraints": {"no_pii": True, "synthetic_only": True},
    }

    with pytest.raises(OrchestratorError, match="Multi-agent review gate failed"):
        await orchestrator.execute_run(problem, config={"enable_multi_review": True})


def test_cli_pvs_review_command_passes_and_saves_report(
    payment_spec: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """pvs review <spec.yaml> --use-mock runs all 4 reviewers and writes consolidated report."""
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(yaml.safe_dump(payment_spec, sort_keys=False), encoding="utf-8")
    out_file = tmp_path / "review_report.yaml"

    rc = cli_main(
        [
            "review",
            str(spec_file),
            "--output",
            str(out_file),
            "--use-mock",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "Verdict:      APPROVED" in out
    assert "Gate:         PASS" in out
    assert out_file.exists()
    saved = yaml.safe_load(out_file.read_text(encoding="utf-8"))
    assert len(saved["reviewers_executed"]) == 4


def test_cli_pvs_run_with_multi_review_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pvs run <problem.yaml> --multi-review completes end-to-end with consolidated review."""
    monkeypatch.setenv("PVS_DB_PATH", str(tmp_path / "pvs.db"))
    monkeypatch.setenv("PVS_ARTIFACT_ROOT", str(tmp_path / "artifacts"))

    rc = cli_main(
        [
            "run",
            str(REPO_ROOT / "examples" / "greenfield" / "problem_payment_flow.yaml"),
            "--multi-review",
        ]
    )
    assert rc == 0
    consolidated = (
        tmp_path
        / "artifacts"
        / "runs"
        / "problem-acme-payment-notifications"
        / "consolidated_review.yaml"
    )
    assert consolidated.exists()
