"""
CLI command implementations.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.agents.architecture_review_agent import ArchitectureReviewAgent
from product_value_system.agents.discovery_agent import DiscoveryAgent
from product_value_system.agents.security_review_agent import SecurityReviewAgent
from product_value_system.agents.specification_agent import SpecificationAgent
from product_value_system.cli.output import (
    print_error,
    print_status,
    print_success,
    print_table,
)
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.evaluation.deterministic_judges import SchemaValidationJudge
from product_value_system.evaluation.pipeline import EvaluationPipeline
from product_value_system.harness.budget import (
    Budget,
    BudgetExhausted,
    EmergencyStopTriggered,
)
from product_value_system.harness.harness import Harness
from product_value_system.orchestrator.checkpoints import CheckpointManager
from product_value_system.orchestrator.controller import ValueStreamOrchestrator
from product_value_system.persistence.database import Database
from product_value_system.persistence.models import RunRecord, StageRecord
from product_value_system.persistence.repository import ValueStreamRepository
from product_value_system.policy.engine import PolicyEngine


def _repo_root() -> Path:
    """Resolve repository root directory."""
    return Path(__file__).parent.parent.parent.parent


def load_config(args: argparse.Namespace) -> dict[str, Any]:
    """Load configuration from file and environment."""
    config: dict[str, Any] = {}

    config_path = getattr(args, "config", None) or (Path.home() / ".pvs" / "config.yaml")
    if Path(config_path).exists():
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    if "PVS_DB_PATH" in os.environ:
        config["db_path"] = os.environ["PVS_DB_PATH"]
    if "PVS_ARTIFACT_ROOT" in os.environ:
        config["artifact_root"] = os.environ["PVS_ARTIFACT_ROOT"]

    root = _repo_root()
    config.setdefault("db_path", "pvs.db")
    config.setdefault("artifact_root", "artifacts")
    config.setdefault("migrations_dir", str(root / "migrations"))
    config.setdefault("schemas_dir", str(root / "schemas"))
    config.setdefault("policies_dir", str(root / "policies"))

    return config


def init_command(args: argparse.Namespace) -> int:
    """Initialize new project."""
    output_path = Path(args.output)

    if output_path.exists():
        print_error(f"File already exists: {output_path}")
        return 1

    template: dict[str, Any] = {
        "id": "problem-payment-fees-001",
        "version": 1,
        "created": "2026-11-18T10:00:00Z",
        "statement": (
            "Small merchants struggle with high payment processing fees and "
            "complex manual settlement reconciliation."
        ),
        "context": {
            "user_segment": "Small merchants (1-10 employees)",
            "business_context": "Strategic initiative to increase SMB market share by 15%",
        },
        "constraints": {
            "no_pii": True,
            "synthetic_only": True,
            "max_cost_usd": 50.0,
            "deadline": "2026-12-31",
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(template, f, sort_keys=False)

    print_success(f"Created problem template: {output_path}")
    print("\nNext steps:")
    print("1. Edit the problem statement")
    print(f"2. Run: pvs run {output_path}")

    return 0


def _build_demo_harness_factory(budget: Budget | None = None) -> Any:
    """Build deterministic harness factory for local/synthetic value stream runs."""
    outcome_resp = json.dumps(
        {
            "hypothesis": (
                "Reducing payment fees from 3.2% to 2.5% and automating daily "
                "reconciliation will increase merchant retention by 15% within 6 months"
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
                    {
                        "metric": "Transaction success rate",
                        "threshold": 99.5,
                        "direction": "above",
                    }
                ],
            },
            "evidence_requirements": [
                {
                    "type": "quantitative",
                    "description": "Synthetic merchant cohort fee sensitivity simulation",
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
                    "description": "Calculate payment fee at 2.5% with itemized breakdown",
                    "priority": "must-have",
                    "acceptance_criteria": [
                        "Fee equals 2.5% of gross amount",
                        "Net settlement equals gross minus fee",
                    ],
                    "linked_to": ["primary_metric"],
                }
            ],
            "architecture_decisions": [
                {
                    "id": "AD-001",
                    "decision": "Use Decimal arithmetic for fee calculation",
                    "rationale": "Avoid floating-point rounding drift",
                }
            ],
            "quality_attributes": {
                "performance": {"latency_ms": 100, "throughput_rps": 500},
                "security": ["Authentication required", "Audit trail enabled"],
                "scalability": ["Support 100K merchants"],
                "reliability": ["99.9% uptime target"],
            },
            "testability_criteria": {
                "unit_tests": ["Test fee calculation with Decimal precision"],
                "integration_tests": ["Test end-to-end settlement reconciliation"],
                "acceptance_tests": ["Verify 2.5% fee applied accurately"],
            },
            "constraints": {
                "technical": ["Python 3.12+", "Docker containers"],
                "operational": ["Single-machine MVP deployment"],
                "business": ["Max $50 run budget"],
            },
        }
    )

    review_resp = json.dumps(
        {
            "findings": [],
            "overall_verdict": "approve",
            "summary": "Specification meets architectural and security requirements",
        }
    )

    def factory(stage_name: str) -> Harness:
        if stage_name == "outcome_hypothesis":
            return Harness(
                model_adapter=MockModelAdapter(responses=[outcome_resp]),
                budget=budget,
            )
        if stage_name == "specification":
            return Harness(
                model_adapter=MockModelAdapter(responses=[spec_resp]),
                budget=budget,
            )
        return Harness(
            model_adapter=MockModelAdapter(responses=[review_resp]),
            budget=budget,
        )

    return factory


def run_command(args: argparse.Namespace) -> int:
    """Execute value stream run."""
    # Pre-flight emergency stop and CLI budget guard check
    budget = Budget(
        max_usd=getattr(args, "max_cost", None),
        daily_limit_usd=getattr(args, "daily_budget", None),
        monthly_limit_usd=getattr(args, "monthly_budget", None),
    )
    try:
        budget.check_before_spend(0.0)
    except (EmergencyStopTriggered, BudgetExhausted) as exc:
        print_error(f"Run blocked by budget guard: {exc}")
        return 1

    problem_path = Path(args.problem)

    if not problem_path.exists():
        print_error(f"Problem file not found: {problem_path}")
        return 1

    with open(problem_path, encoding="utf-8") as f:
        problem = yaml.safe_load(f)

    config = load_config(args)

    try:
        schema_validator = SchemaValidator(Path(config["schemas_dir"]))
        schema_validator.validate(problem, "problem.schema.json")
    except Exception as e:
        print_error(f"Problem validation failed: {e}")
        return 1

    db = Database(
        Path(config["db_path"]),
        Path(config["migrations_dir"]),
    )
    db.connect()
    db.migrate()
    repository = ValueStreamRepository(db)

    checkpoint_manager = CheckpointManager(
        Path(config["artifact_root"]) / "checkpoints"
    )
    policy_engine = PolicyEngine(Path(config["policies_dir"]))
    eval_pipeline = EvaluationPipeline(
        judges=[SchemaValidationJudge(schema_validator)],
        passing_threshold=0.7,
    )

    agents = {
        "discovery": DiscoveryAgent(schema_validator),
        "specification": SpecificationAgent(schema_validator),
        "review_architecture": ArchitectureReviewAgent(schema_validator),
        "review_security": SecurityReviewAgent(schema_validator),
    }

    orchestrator = ValueStreamOrchestrator(
        agents=agents,
        evaluation_pipeline=eval_pipeline,
        policy_engine=policy_engine,
        checkpoint_manager=checkpoint_manager,
        artifact_root=Path(config["artifact_root"]),
        harness_factory=_build_demo_harness_factory(budget=budget),
    )

    print(f"Starting value stream run for: {problem['statement'][:60]}...")

    try:
        run = asyncio.run(orchestrator.execute_run(problem))

        assert run.started_at is not None
        repository.save_run(
            RunRecord(
                id=run.id,
                problem_id=run.problem_id,
                status=run.status.value,
                started_at=run.started_at,
                completed_at=run.completed_at,
                total_cost_usd=run.total_cost_usd,
                outcome_achieved=run.outcome_achieved,
                artifact_dir=run.artifact_dir,
            )
        )
        for stage in run.stages.values():
            repository.save_stage(
                StageRecord(
                    id=str(uuid4()),
                    run_id=run.id,
                    stage_name=stage.stage_name,
                    agent_name=stage.agent_name,
                    status=stage.status.value,
                    started_at=stage.started_at,
                    completed_at=stage.completed_at,
                    artifact_path=stage.artifact_path,
                    cost_usd=stage.cost_usd,
                    error=stage.error,
                    metadata=stage.metadata,
                )
            )

        print_success(f"Run completed: {run.id}")
        print(f"Status: {run.status.value}")
        print(f"Cost: ${run.total_cost_usd:.2f}")
        print(f"Artifacts: {run.artifact_dir}")

        return 0

    except Exception as e:
        print_error(f"Run failed: {e}")
        return 1
    finally:
        db.close()


def status_command(args: argparse.Namespace) -> int:
    """Show run status."""
    run_id = str(args.run_id)
    config = load_config(args)

    db = Database(
        Path(config["db_path"]),
        Path(config["migrations_dir"]),
    )
    db.connect()
    db.migrate()

    try:
        repository = ValueStreamRepository(db)
        run = repository.get_run(run_id)

        if not run:
            print_error(f"Run not found: {run_id}")
            return 1

        print_status(run)

        stages = repository.get_stages(run_id)
        if stages:
            print("\nStages:")
            stage_data = [
                {
                    "Stage": stage.stage_name,
                    "Status": stage.status,
                    "Agent": stage.agent_name or "-",
                    "Cost": f"${stage.cost_usd:.2f}",
                }
                for stage in stages
            ]
            print_table(stage_data)

        return 0
    finally:
        db.close()


def list_command(args: argparse.Namespace) -> int:
    """List recent runs."""
    config = load_config(args)

    db = Database(
        Path(config["db_path"]),
        Path(config["migrations_dir"]),
    )
    db.connect()
    db.migrate()

    try:
        repository = ValueStreamRepository(db)
        runs = repository.list_runs(limit=int(args.limit))

        if not runs:
            print("No runs found")
            return 0

        run_data = [
            {
                "Run ID": run.id[:12],
                "Problem": run.problem_id[:20],
                "Status": run.status,
                "Started": run.started_at.strftime("%Y-%m-%d %H:%M"),
                "Cost": f"${run.total_cost_usd:.2f}",
            }
            for run in runs
        ]

        print_table(run_data)
        return 0
    finally:
        db.close()


def show_command(args: argparse.Namespace) -> int:
    """Show detailed run information."""
    run_id = str(args.run_id)
    config = load_config(args)

    db = Database(
        Path(config["db_path"]),
        Path(config["migrations_dir"]),
    )
    db.connect()
    db.migrate()

    try:
        repository = ValueStreamRepository(db)
        run = repository.get_run(run_id)

        if not run:
            print_error(f"Run not found: {run_id}")
            return 1

        print(f"Run ID: {run.id}")
        print(f"Problem: {run.problem_id}")
        print(f"Status: {run.status}")
        print(f"Started: {run.started_at}")
        print(f"Completed: {run.completed_at or 'In progress'}")
        print(f"Total Cost: ${run.total_cost_usd:.2f}")
        print(f"Artifacts: {run.artifact_dir}")

        breakdown = repository.get_cost_breakdown(run_id)
        if breakdown:
            print("\nCost Breakdown:")
            for agent, cost in breakdown.items():
                print(f"  {agent}: ${cost:.2f}")

        stages = repository.get_stages(run_id)
        if stages:
            print("\nStages:")
            for stage in stages:
                print(f"  [{stage.status}] {stage.stage_name}")
                if stage.agent_name:
                    print(f"    Agent: {stage.agent_name}")
                if stage.artifact_path:
                    print(f"    Artifact: {stage.artifact_path}")
                if stage.error:
                    print(f"    Error: {stage.error}")

        return 0
    finally:
        db.close()


def validate_command(args: argparse.Namespace) -> int:
    """Validate artifact against schema."""
    artifact_path = Path(args.artifact)
    artifact_type = str(args.type)

    if not artifact_path.exists():
        print_error(f"Artifact not found: {artifact_path}")
        return 1

    with open(artifact_path, encoding="utf-8") as f:
        if artifact_path.suffix == ".json":
            artifact = json.load(f)
        else:
            artifact = yaml.safe_load(f)

    config = load_config(args)

    try:
        schema_validator = SchemaValidator(Path(config["schemas_dir"]))
        schema_validator.validate(artifact, f"{artifact_type}.schema.json")

        print_success(f"Artifact is valid: {artifact_type}")
        return 0

    except Exception as e:
        print_error(f"Validation failed: {e}")
        return 1
