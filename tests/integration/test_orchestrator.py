"""
Orchestrator integration tests (P014).
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.agents.discovery_agent import DiscoveryAgent
from product_value_system.agents.specification_agent import SpecificationAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.evaluation.deterministic_judges import SchemaValidationJudge
from product_value_system.evaluation.pipeline import EvaluationPipeline
from product_value_system.harness.harness import Harness
from product_value_system.orchestrator.checkpoints import CheckpointManager
from product_value_system.orchestrator.controller import (
    OrchestratorError,
    ValueStreamOrchestrator,
)
from product_value_system.orchestrator.state_machine import (
    RunStatus,
    StageResult,
    StageStatus,
    ValueStreamRun,
    ValueStreamStateMachine,
)
from product_value_system.policy.engine import PolicyEngine


@pytest.fixture
def checkpoint_manager(tmp_path: Path) -> CheckpointManager:
    """Checkpoint manager."""
    return CheckpointManager(tmp_path / "checkpoints")


@pytest.fixture
def artifact_root(tmp_path: Path) -> Path:
    """Artifact root directory."""
    return tmp_path / "artifacts"


@pytest.mark.integration
def test_state_machine_progression() -> None:
    """Test state machine stage progression."""
    run = ValueStreamRun(problem_id="test-001")
    state_machine = ValueStreamStateMachine(run)

    assert state_machine.get_next_stage() == "problem_analysis"

    stage = StageResult(
        stage_name="problem_analysis",
        status=StageStatus.COMPLETED,
        started_at=datetime.now(UTC),
    )
    run.add_stage_result(stage)
    run.current_stage = "problem_analysis"

    assert state_machine.get_next_stage() == "outcome_hypothesis"
    assert state_machine.can_advance("problem_analysis") is True


@pytest.mark.integration
def test_checkpoint_save_and_load(checkpoint_manager: CheckpointManager) -> None:
    """Test checkpoint save and load."""
    run = ValueStreamRun(
        problem_id="test-001",
        status=RunStatus.RUNNING,
        started_at=datetime.now(UTC),
    )

    checkpoint_manager.save_checkpoint(run)

    loaded_run = checkpoint_manager.load_checkpoint(run.id)

    assert loaded_run is not None
    assert loaded_run.id == run.id
    assert loaded_run.problem_id == run.problem_id
    assert loaded_run.status == RunStatus.RUNNING


@pytest.mark.integration
@pytest.mark.asyncio
async def test_orchestrator_executes_problem_analysis(
    checkpoint_manager: CheckpointManager,
    artifact_root: Path,
) -> None:
    """Test orchestrator executes first stage and saves checkpoint on failure."""
    orchestrator = ValueStreamOrchestrator(
        agents={},
        evaluation_pipeline=None,
        policy_engine=None,
        checkpoint_manager=checkpoint_manager,
        artifact_root=artifact_root,
    )

    problem = {
        "id": "problem-test-001",
        "version": 1,
        "statement": "Test problem",
        "context": {},
        "constraints": {},
    }

    with pytest.raises(OrchestratorError):
        await orchestrator.execute_run(problem)

    checkpoints = list(checkpoint_manager.checkpoint_dir.glob("*.json"))
    assert len(checkpoints) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_orchestrator_recovery(
    checkpoint_manager: CheckpointManager,
    artifact_root: Path,
) -> None:
    """Test orchestrator can recover from checkpoint."""
    run = ValueStreamRun(
        problem_id="test-001",
        status=RunStatus.RUNNING,
        artifact_dir=str(artifact_root / "runs" / "test-001"),
    )

    assert run.artifact_dir is not None
    Path(run.artifact_dir).mkdir(parents=True, exist_ok=True)

    problem_path = Path(run.artifact_dir) / "problem.yaml"
    with open(problem_path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"id": "test-001", "version": 1, "statement": "Test"}, f)

    checkpoint_manager.save_checkpoint(run)

    orchestrator = ValueStreamOrchestrator(
        agents={},
        evaluation_pipeline=None,
        policy_engine=None,
        checkpoint_manager=checkpoint_manager,
        artifact_root=artifact_root,
    )

    with pytest.raises(OrchestratorError):
        await orchestrator.resume_run(run.id)

    loaded_run = checkpoint_manager.load_checkpoint(run.id)
    assert loaded_run is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_orchestrator_complete_value_stream_run(
    checkpoint_manager: CheckpointManager,
    artifact_root: Path,
) -> None:
    """Test complete end-to-end value stream run through all 10 stages."""
    schema_dir = Path(__file__).parent.parent.parent / "schemas"
    validator = SchemaValidator(schema_dir)
    policy_engine = PolicyEngine()

    discovery_agent = DiscoveryAgent(validator)
    spec_agent = SpecificationAgent(validator)

    outcome_json = json.dumps(
        {
            "hypothesis": (
                "Reducing payment fees from 3.2% to 2.5% will increase merchant "
                "retention by 15% within 6 months"
            ),
            "success_criteria": {
                "primary_metric": {
                    "name": "Merchant retention rate",
                    "unit": "percentage",
                    "measurement_method": "Monthly cohort retention",
                },
                "baseline": 85.0,
                "target": 97.75,
                "guardrails": [],
            },
            "evidence_requirements": [
                {
                    "type": "quantitative",
                    "description": "Merchant survey",
                    "mandatory": True,
                }
            ],
        }
    )

    spec_json = json.dumps(
        {
            "functional_requirements": [
                {
                    "id": "FR-001",
                    "description": "Reduce fee calculation to 2.5%",
                    "priority": "must-have",
                    "acceptance_criteria": ["Fee updated to 2.5%"],
                    "linked_to": ["primary_metric"],
                }
            ],
            "architecture_decisions": [
                {
                    "id": "AD-001",
                    "decision": "Use decimal arithmetic",
                    "rationale": "Exact financial calculation",
                }
            ],
            "quality_attributes": {
                "performance": {"latency_ms": 100},
                "security": ["Authentication required"],
                "scalability": ["100K merchants"],
                "reliability": ["99.9% uptime"],
            },
            "testability_criteria": {
                "unit_tests": ["Test fee calculation"],
                "integration_tests": ["Test checkout"],
                "acceptance_tests": ["Verify fee rate"],
            },
            "constraints": {
                "technical": ["Python 3.12+"],
                "operational": ["Docker"],
                "business": ["$50 max"],
            },
        }
    )

    def harness_factory(stage_name: str) -> Harness:
        if stage_name == "outcome_hypothesis":
            return Harness(model_adapter=MockModelAdapter(responses=[outcome_json]))
        return Harness(model_adapter=MockModelAdapter(responses=[spec_json]))

    eval_pipeline = EvaluationPipeline(
        judges=[SchemaValidationJudge(validator)],
        passing_threshold=0.7,
    )

    orchestrator = ValueStreamOrchestrator(
        agents={
            "discovery": discovery_agent,
            "specification": spec_agent,
        },
        evaluation_pipeline=eval_pipeline,
        policy_engine=policy_engine,
        checkpoint_manager=checkpoint_manager,
        artifact_root=artifact_root,
        harness_factory=harness_factory,
    )

    problem: dict[str, Any] = {
        "id": "problem-payment-fees-001",
        "version": 1,
        "created": datetime.now(UTC).isoformat(),
        "statement": "Small merchants struggle with high payment processing fees.",
        "context": {
            "user_segment": "Small merchants",
            "business_context": "Increase SMB market share",
        },
        "constraints": {
            "no_pii": True,
            "synthetic_only": True,
        },
    }

    run = await orchestrator.execute_run(
        problem,
        config={"require_human_approval": True},
    )

    assert run.status == RunStatus.COMPLETED
    assert run.outcome_achieved is True
    assert len(run.stages) == 10
    assert Path(run.artifact_dir or "").joinpath("problem.yaml").exists()
    assert Path(run.artifact_dir or "").joinpath("outcome.yaml").exists()
    assert Path(run.artifact_dir or "").joinpath("specification.yaml").exists()
    assert len(orchestrator.events) >= 20
