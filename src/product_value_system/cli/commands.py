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
        "discovery": DiscoveryAgent(
            schema_validator,
            enable_simulation=bool(getattr(args, "enable_simulation", False)),
            simulation_budget_usd=float(getattr(args, "simulation_budget", 10.0)),
        ),
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


def run_simulation_command(args: argparse.Namespace) -> int:
    """Execute a synthetic customer simulation scenario from YAML."""
    from product_value_system.simulation.persona_factory import PersonaFactory
    from product_value_system.simulation.scenario_runner import ScenarioRunner
    from product_value_system.simulation.tinytroupe_adapter import TinyTroupeAdapter

    scenario_path = Path(args.scenario)
    if not scenario_path.exists():
        print_error(f"Scenario file not found: {scenario_path}")
        return 1

    templates_path = (
        Path(args.templates)
        if getattr(args, "templates", None) is not None
        else _repo_root() / "config" / "simulation" / "persona_templates.yaml"
    )

    try:
        adapter = TinyTroupeAdapter(max_cost_usd=float(getattr(args, "max_cost", 10.0)))
        factory = PersonaFactory(templates_path)
        runner = ScenarioRunner(adapter=adapter, factory=factory)
        result = asyncio.run(runner.run_scenario(scenario_path))

        print_success(f"Simulation completed: {result.scenario_name}")
        print(f"Population: {result.population_size} personas")
        print(f"Interactions: {len(result.interactions)}")
        print(f"Cost: ${result.cost_usd:.4f}")
        print("Quality metrics:")
        for metric, val in result.quality_metrics.items():
            print(f"  {metric}: {val:.3f}")
        print(f"Disclaimer: {result.disclaimer}")
        return 0
    except Exception as exc:
        print_error(f"Simulation failed: {exc}")
        return 1


def measure_outcome_command(args: argparse.Namespace) -> int:
    """Measure post-deployment outcome, attribute cost, and evaluate feedback alerts."""
    from datetime import UTC, datetime, timedelta

    from product_value_system.harness.context import ExecutionContext
    from product_value_system.learning.analytics_adapter import (
        AnalyticsAdapter,
        MockAnalyticsAdapter,
        PrometheusAnalyticsAdapter,
        SQLAnalyticsAdapter,
    )
    from product_value_system.learning.feedback_loop import FeedbackLoop
    from product_value_system.learning.measurement_agent import MeasurementAgent
    from product_value_system.learning.outcome_tracker import OutcomeTracker

    outcome_id = str(args.outcome_id)
    window_days = int(getattr(args, "measurement_window", 30))
    baseline_window_days = int(getattr(args, "baseline_window", 30))
    dev_cost = float(getattr(args, "dev_cost", 12.50))

    # Build or load outcome hypothesis
    outcome_file = getattr(args, "outcome_file", None)
    if outcome_file is not None:
        outcome_path = Path(outcome_file)
        if not outcome_path.exists():
            print_error(f"Outcome file not found: {outcome_path}")
            return 1
        with open(outcome_path, encoding="utf-8") as f:
            hypothesis: dict[str, Any] = yaml.safe_load(f) or {}
        if "success_criteria" in hypothesis:
            sc = hypothesis["success_criteria"]
            pm = sc.get("primary_metric", {})
            metric_name = str(getattr(args, "metric", None) or pm.get("name", "primary_metric"))
            baseline = float(
                args.baseline if args.baseline is not None else sc.get("baseline", 85.0)
            )
            target = float(args.target if args.target is not None else sc.get("target", 97.75))
            pm["name"] = metric_name
            sc["baseline"] = baseline
            sc["target"] = target
        else:
            metric_name = str(getattr(args, "metric", None) or "Merchant retention rate")
            baseline = float(args.baseline if args.baseline is not None else 85.0)
            target = float(args.target if args.target is not None else 97.75)
            hypothesis["primary_metric"] = {
                "name": metric_name,
                "baseline": baseline,
                "target": target,
            }
    else:
        metric_name = str(getattr(args, "metric", None) or "Merchant retention rate")
        baseline = float(args.baseline if args.baseline is not None else 85.0)
        target = float(args.target if args.target is not None else 97.75)
        hypothesis = {
            "id": outcome_id,
            "success_criteria": {
                "primary_metric": {
                    "name": metric_name,
                    "unit": "percentage",
                    "measurement_method": "analytics",
                },
                "baseline": baseline,
                "target": target,
                "guardrails": [],
            },
        }

    # Resolve analytics adapter
    db_url = getattr(args, "db_url", None)
    prometheus_url = getattr(args, "prometheus_url", None)
    use_mock = bool(getattr(args, "use_mock_analytics", False))

    adapter: AnalyticsAdapter
    prom_adapter: PrometheusAnalyticsAdapter | None = None
    if db_url:
        adapter = SQLAnalyticsAdapter(str(db_url))
    elif prometheus_url:
        prom_adapter = PrometheusAnalyticsAdapter(str(prometheus_url))
        adapter = prom_adapter
    elif use_mock:
        if getattr(args, "current_value", None) is not None:
            current_val = float(args.current_value)
        else:
            current_val = (
                target + abs(target - baseline) * 0.05
                if target >= baseline
                else target * 0.95
            )
        mock_metrics: dict[str, float] = {metric_name: round(current_val, 4)}
        for g in hypothesis.get("success_criteria", {}).get("guardrails", []):
            g_name = str(g.get("metric", ""))
            g_thresh = g.get("threshold")
            if g_name and isinstance(g_thresh, (int, float)):
                g_dir = str(g.get("direction", "above")).lower()
                mock_metrics[g_name] = (
                    round(float(g_thresh) + 0.3, 4)
                    if g_dir == "above"
                    else round(float(g_thresh) * 0.9, 4)
                )
        adapter = MockAnalyticsAdapter(mock_metrics)
    else:
        print_error(
            "No analytics source configured. "
            "Pass --use-mock-analytics, --db-url, or --prometheus-url."
        )
        return 1

    async def _run_measurement() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        try:
            tracker = OutcomeTracker(adapter)
            deploy_date = datetime.now(UTC) - timedelta(days=window_days)
            tracker.register_deployment(
                outcome_id=outcome_id,
                deployment_date=deploy_date,
                outcome_hypothesis=hypothesis,
                development_cost_usd=dev_cost,
            )
            agent = MeasurementAgent(tracker)
            loop = FeedbackLoop(tracker)

            ctx = ExecutionContext(
                run_id=f"measure-{outcome_id}",
                stage_name="measure",
                agent_name=agent.name,
            )
            harness = Harness(context=ctx)
            result = await agent.execute(
                input_artifact={
                    "outcome_id": outcome_id,
                    "measurement_window_days": window_days,
                    "baseline_window_days": baseline_window_days,
                },
                harness=harness,
                context=ctx,
            )
            if not result.success:
                raise RuntimeError(result.error or "Measurement failed")

            measurement_obj = tracker.measurements[outcome_id]
            alerts = await loop.evaluate_measurement(measurement_obj)
            loop.record_feedback(measurement_obj)
            return result.output_artifact, [a.to_dict() for a in alerts]
        finally:
            if prom_adapter is not None:
                await prom_adapter.close()

    try:
        output_artifact, alerts = asyncio.run(_run_measurement())

        print_success(f"Outcome measurement completed: {outcome_id}")
        print(f"Metric: {output_artifact['primary_metric_name']}")
        print(
            f"Baseline: {output_artifact['baseline_value']:.2f} | "
            f"Current: {output_artifact['current_value']:.2f} | "
            f"Target: {output_artifact['target_value']:.2f}"
        )
        print(
            f"Status: {output_artifact['status']} (p={output_artifact['p_value']:.4f})"
        )
        if output_artifact.get("confidence_interval") is not None:
            ci = output_artifact["confidence_interval"]
            print(f"95% CI: [{ci[0]:.4f}, {ci[1]:.4f}]")

        cost_attr = output_artifact.get("cost_attribution", {})
        print(
            f"Cost Attribution: dev=${cost_attr.get('development_cost_usd', 0.0):.2f}, "
            f"improvement={cost_attr.get('improvement', 0.0):+.2f}, "
            f"cost/unit={cost_attr.get('cost_per_unit_improvement')}"
        )

        if output_artifact.get("learning"):
            print("\nLearnings:")
            for item in output_artifact["learning"]:
                print(f"  - {item}")

        if output_artifact.get("recommendations"):
            print("\nRecommendations:")
            for rec in output_artifact["recommendations"]:
                print(f"  - {rec}")

        if alerts:
            print("\nAlerts:")
            for alert in alerts:
                print(f"  [{alert['severity'].upper()}] {alert['message']}")

        output_path = getattr(args, "output", None)
        if output_path is not None:
            out_file = Path(output_path)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            with open(out_file, "w", encoding="utf-8") as f:
                yaml.safe_dump(output_artifact, f, sort_keys=False)
            print_success(f"Saved measurement report: {out_file}")

        return 0
    except Exception as exc:
        print_error(f"Outcome measurement failed: {exc}")
        return 1
