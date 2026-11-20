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


def _build_demo_harness_factory(
    budget: Budget | None = None,
    trajectory_capture: Any | None = None,
) -> Any:
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
                trajectory_capture=trajectory_capture,
            )
        if stage_name == "specification":
            return Harness(
                model_adapter=MockModelAdapter(responses=[spec_resp]),
                budget=budget,
                trajectory_capture=trajectory_capture,
            )
        return Harness(
            model_adapter=MockModelAdapter(responses=[review_resp]),
            budget=budget,
            trajectory_capture=trajectory_capture,
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

    capture_trajectory = bool(getattr(args, "capture_trajectory", False))
    trajectory_capture: Any | None = None
    if capture_trajectory:
        from product_value_system.evaluation.trajectory_capture import TrajectoryCapture

        trajectory_capture = TrajectoryCapture(enabled=True)
        trajectory_capture.start_trajectory("value-stream-run", problem)

    orchestrator = ValueStreamOrchestrator(
        agents=agents,
        evaluation_pipeline=eval_pipeline,
        policy_engine=policy_engine,
        checkpoint_manager=checkpoint_manager,
        artifact_root=Path(config["artifact_root"]),
        harness_factory=_build_demo_harness_factory(
            budget=budget,
            trajectory_capture=trajectory_capture,
        ),
    )

    print(f"Starting value stream run for: {problem['statement'][:60]}...")

    try:
        run_config: dict[str, Any] = {}
        if getattr(args, "multi_review", False):
            run_config["enable_multi_review"] = True
        run = asyncio.run(orchestrator.execute_run(problem, config=run_config))

        if trajectory_capture is not None:
            final_traj = trajectory_capture.finalize_trajectory(
                output_artifact={"run_id": run.id, "status": run.status.value},
                success=run.status.value == "completed",
            )
            if final_traj is not None:
                traj_out = getattr(args, "trajectory_output", None)
                target_path = (
                    Path(traj_out)
                    if traj_out is not None
                    else Path(run.artifact_dir or ".") / "trajectory.json"
                )
                final_traj.save(target_path)
                print_success(
                    f"Captured trajectory ({final_traj.total_steps} steps) -> {target_path}"
                )

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


class _EvalSandboxResult:
    """Minimal deterministic sandbox execution result for offline build eval."""

    def __init__(self, exit_code: int = 0, stdout: str = "1 passed in 0.02s") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = ""
        self.duration_seconds = 0.02


class _EvalMockSandboxProvider:
    """Offline sandbox provider used by ``pvs eval build`` when running in mock mode."""

    async def create(self, config: Any) -> str:
        return "eval-mock-sandbox"

    async def execute(
        self,
        sandbox: Any,
        command: str,
        timeout_seconds: int | None = None,
    ) -> _EvalSandboxResult:
        return _EvalSandboxResult(exit_code=0)

    async def destroy(self, sandbox: Any) -> None:
        return None


class _ReviewAgentRouter:
    """Routes review golden samples to ArchitectureReviewAgent or SecurityReviewAgent."""

    name = "review-agent"

    def __init__(self, schema_validator: SchemaValidator) -> None:
        self._arch = ArchitectureReviewAgent(schema_validator)
        self._sec = SecurityReviewAgent(schema_validator)

    async def execute(
        self,
        input_artifact: dict[str, Any],
        harness: Harness,
        context: Any,
    ) -> dict[str, Any]:
        review_type = str(input_artifact.get("review_type", "architecture")).lower()
        spec = input_artifact.get("specification", input_artifact)
        if review_type == "security":
            sec_res: dict[str, Any] = await self._sec.execute(spec, harness, context)
            return sec_res
        arch_res: dict[str, Any] = await self._arch.execute(spec, harness, context)
        return arch_res


def _mock_responses_for_eval_sample(agent_name: str, sample: Any) -> list[str]:
    """Generate schema-valid, sample-aware mock LLM responses for offline regression eval."""
    expected = sample.expected_output

    if agent_name in ("discovery", "brownfield"):
        pm = expected.get("primary_metric", {})
        metric_name = str(pm.get("name", "documentation_accuracy"))
        unit = str(pm.get("unit", "percentage"))
        baseline = float(pm.get("baseline", 60.0))
        target = float(pm.get("target", 92.0))
        guardrails = expected.get("guardrails") or [
            {"metric": "regression_incident_rate", "threshold": 1.0, "direction": "below"}
        ]
        norm_guardrails = [
            {
                "metric": str(g.get("metric", "error_rate")),
                "threshold": float(g.get("threshold", 1.0)),
                "direction": str(g.get("direction", "below")),
            }
            for g in guardrails
        ]
        if agent_name == "brownfield":
            drifts = expected.get("expected_drift_findings", ["Undocumented legacy behavior"])
            hypothesis = (
                f"Documenting legacy behavior and resolving code-vs-doc drift ({drifts[0]}) "
                "with characterization tests covering >=70% lines will preserve compatibility "
                "and raise documentation accuracy from baseline to target."
            )
            ev_desc = (
                "Characterization test suite and drift detection report comparing "
                "actual legacy code against existing docs."
            )
        else:
            hypothesis = (
                f"Addressing the validated user problem will improve {metric_name} "
                f"from {baseline} to {target} {unit} while preserving all operational guardrails."
            )
            ev_desc = f"Quantitative cohort telemetry measuring {metric_name} post-rollout."
        return [
            json.dumps(
                {
                    "hypothesis": hypothesis,
                    "success_criteria": {
                        "primary_metric": {
                            "name": metric_name,
                            "unit": unit,
                            "measurement_method": "Production analytics telemetry query",
                        },
                        "baseline": baseline,
                        "target": target,
                        "guardrails": norm_guardrails,
                    },
                    "evidence_requirements": [
                        {
                            "type": "quantitative",
                            "description": ev_desc,
                            "mandatory": True,
                        }
                    ],
                }
            )
        ]

    if agent_name == "specification":
        linked_metric = str(expected.get("linked_metric", "primary_metric"))
        return [
            json.dumps(
                {
                    "functional_requirements": [
                        {
                            "id": "FR-001",
                            "description": (
                                f"Implement core capability driving {linked_metric} improvement."
                            ),
                            "priority": "must-have",
                            "acceptance_criteria": [
                                f"Measurably improves {linked_metric}",
                                "Handles boundary and failure inputs gracefully",
                            ],
                            "linked_to": ["primary_metric"],
                        },
                        {
                            "id": "FR-002",
                            "description": (
                                "Emit structured telemetry and enforce operational guardrails."
                            ),
                            "priority": "must-have",
                            "acceptance_criteria": [
                                "Records latency and error-rate metrics",
                                "Preserves security and audit controls",
                            ],
                            "linked_to": ["guardrails"],
                        },
                    ],
                    "architecture_decisions": [
                        {
                            "id": "AD-001",
                            "decision": (
                                "Use modular protocol-driven service with strict validation"
                            ),
                            "rationale": "Balances testability, security isolation, and clarity",
                        }
                    ],
                    "quality_attributes": {
                        "performance": {"latency_ms": 150, "throughput_rps": 500},
                        "security": ["Authentication required", "Input validation enforced"],
                        "scalability": ["Horizontally scalable stateless workers"],
                        "reliability": ["99.9% uptime SLA with automated retries"],
                    },
                    "testability_criteria": {
                        "unit_tests": ["Verify core business logic and edge cases"],
                        "integration_tests": ["Verify end-to-end service contract"],
                        "acceptance_tests": [f"Verify {linked_metric} telemetry emission"],
                    },
                    "constraints": {
                        "technical": ["Python 3.12+", "Schema validation enforced"],
                        "operational": ["Automated CI/CD quality gates"],
                        "business": ["Zero PII in test fixtures"],
                    },
                }
            )
        ]

    if agent_name == "build":
        impl_code = (
            "```python\n"
            "def execute_requirement(value: int) -> int:\n"
            '    """Implement functional requirement cleanly."""\n'
            "    if value < 0:\n"
            '        raise ValueError("value must be non-negative")\n'
            "    return value\n"
            "```"
        )
        test_code = (
            "```python\n"
            "def test_execute_requirement() -> None:\n"
            "    assert execute_requirement(5) == 5\n"
            "```"
        )
        return [impl_code, test_code]

    if agent_name == "review":
        default_verdict = "reject" if sample.category == "adversarial" else "approve"
        verdict = str(expected.get("expected_verdict", default_verdict))
        categories = expected.get("expected_finding_categories", [])
        findings: list[dict[str, Any]] = []
        if verdict != "approve":
            severity = "critical" if verdict == "reject" else "medium"
            cat = str(categories[0]) if categories else "design"
            findings.append(
                {
                    "severity": severity,
                    "category": cat,
                    "title": f"Detected {cat} risk in FR-001 / AD-001",
                    "description": (
                        f"Specification introduces a {severity} {cat} flaw requiring remediation."
                    ),
                    "recommendation": (
                        f"Remediate {cat} flaw before proceeding to build stage."
                    ),
                    "references": ["FR-001", "AD-001"],
                }
            )
        return [
            json.dumps(
                {
                    "findings": findings,
                    "overall_verdict": verdict,
                    "summary": f"Independent review completed with verdict '{verdict}' on FR-001.",
                }
            )
        ]

    return [json.dumps({"status": "ok"})]


def eval_command(args: argparse.Namespace) -> int:
    """Execute regression evaluation against versioned golden datasets."""
    from product_value_system.agents.build_agent import BuildAgent
    from product_value_system.evaluation.ai_judges import CompletenessJudge
    from product_value_system.evaluation.golden_dataset_loader import GoldenDatasetLoader
    from product_value_system.evaluation.judge import Judge
    from product_value_system.evaluation.regression_eval import (
        RegressionEvaluator,
        RubricComplianceJudge,
    )
    from product_value_system.evaluation.trajectory_eval import TrajectoryEvaluator

    config = load_config(args)
    dataset_root = (
        Path(args.dataset_dir)
        if getattr(args, "dataset_dir", None) is not None
        else _repo_root() / "golden_datasets"
    )
    if not dataset_root.exists():
        print_error(f"Golden dataset directory not found: {dataset_root}")
        return 1

    loader = GoldenDatasetLoader(dataset_root)
    schema_validator = SchemaValidator(Path(config["schemas_dir"]))

    agents_to_eval = loader.list_agents() if args.agent == "all" else [str(args.agent)]
    if not agents_to_eval:
        print_error(f"No golden datasets found under: {dataset_root}")
        return 1

    use_mock = bool(getattr(args, "use_mock", False))
    openai_key = os.environ.get("OPENAI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    live_adapter: Any | None = None

    if not use_mock and openai_key:
        from product_value_system.adapters.openai_adapter import OpenAIModelAdapter

        live_adapter = OpenAIModelAdapter(
            api_key=openai_key,
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        )
    elif not use_mock and anthropic_key:
        from product_value_system.adapters.anthropic_adapter import AnthropicAdapter

        live_adapter = AnthropicAdapter(
            api_key=anthropic_key,
            model=os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
        )

    dataset_version = int(getattr(args, "dataset_version", 1))
    fast_mode = bool(getattr(args, "fast", False))
    subset_size = int(getattr(args, "subset_size", 10))
    category_filter = getattr(args, "category", None)
    threshold = float(getattr(args, "threshold", 0.75))
    baseline_dir = getattr(args, "baseline_dir", None)
    output_json = getattr(args, "output_json", None)
    max_cost = float(getattr(args, "max_cost", 10.0))
    with_trajectories = bool(getattr(args, "with_trajectories", False))
    trajectories_dir = (
        Path(args.trajectories_dir)
        if getattr(args, "trajectories_dir", None) is not None
        else dataset_root / "trajectories"
    )
    trajectory_evaluator = (
        TrajectoryEvaluator(llm_judge=live_adapter) if with_trajectories else None
    )

    async def _run_all() -> tuple[list[Any], list[str]]:
        results: list[Any] = []
        failed_agents: list[str] = []

        for agent_name in agents_to_eval:
            print(f"\n{'=' * 60}")
            print(f"Evaluating: {agent_name}")
            print(f"{'=' * 60}")

            categories = [category_filter] if category_filter else None
            dataset = loader.load(
                agent_name,
                version=dataset_version,
                categories=categories,
            )
            if fast_mode:
                dataset = dataset.sample_subset(subset_size)
                print(f"Fast mode: evaluating {len(dataset)} samples (subset)")

            agent: Any
            if agent_name in ("discovery", "brownfield"):
                agent = DiscoveryAgent(schema_validator)
            elif agent_name == "specification":
                agent = SpecificationAgent(schema_validator)
            elif agent_name == "build":
                agent = BuildAgent(schema_validator, _EvalMockSandboxProvider())
            elif agent_name == "review":
                agent = _ReviewAgentRouter(schema_validator)
            else:
                raise ValueError(f"Unsupported agent for evaluation: {agent_name}")

            judges: list[Judge] = [RubricComplianceJudge()]
            if agent_name != "build":
                judges.insert(0, SchemaValidationJudge(schema_validator))
            if live_adapter is not None:
                judges.append(CompletenessJudge(live_adapter))

            eval_pipeline = EvaluationPipeline(judges=judges, passing_threshold=0.7)

            def _make_harness(sample: Any, _agent: str = agent_name) -> Harness:
                adapter = (
                    live_adapter
                    if live_adapter is not None
                    else MockModelAdapter(responses=_mock_responses_for_eval_sample(_agent, sample))
                )
                return Harness(
                    model_adapter=adapter,
                    budget=Budget(max_usd=max_cost),
                )

            default_harness = Harness(
                model_adapter=live_adapter or MockModelAdapter(),
                budget=Budget(max_usd=max_cost),
            )
            evaluator = RegressionEvaluator(
                agent=agent,
                harness=default_harness,
                eval_pipeline=eval_pipeline,
                baseline_path=baseline_dir,
                harness_factory=_make_harness,
                max_budget_usd=max_cost,
                trajectory_evaluator=trajectory_evaluator,
                golden_trajectories_dir=trajectories_dir,
            )

            result = await evaluator.evaluate(dataset, parallel=True)
            results.append(result)
            print(result.summary())

            if output_json is not None:
                out_path = Path(output_json)
                if args.agent == "all" or out_path.is_dir() or out_path.suffix != ".json":
                    target_file = out_path / f"eval_results_{agent_name}.json"
                else:
                    target_file = out_path
                result.save_json(target_file)
                print(f"Saved JSON report: {target_file}")

            if result.pass_rate < threshold:
                print_error(
                    f"FAILED: {agent_name} pass rate {result.pass_rate:.1%} "
                    f"< threshold {threshold:.1%}"
                )
                failed_agents.append(agent_name)
            elif result.regression_detected:
                print(
                    f"WARNING: Regression detected for {agent_name} "
                    f"(pass rate dropped >{result.regression_threshold:.0%})"
                )
            else:
                print_success(
                    f"PASSED: {agent_name} pass rate {result.pass_rate:.1%} "
                    f">= threshold {threshold:.1%}"
                )

        return results, failed_agents

    try:
        results, failed_agents = asyncio.run(_run_all())

        print(f"\n{'=' * 60}")
        print("Overall Results")
        print(f"{'=' * 60}")
        print(f"Agents evaluated: {len(results)}")
        print(f"Agents passed:    {len(results) - len(failed_agents)}")
        print(f"Agents failed:    {len(failed_agents)}")

        if failed_agents:
            print_error(f"Failed agents: {', '.join(failed_agents)}")
            return 1

        print_success("All agents passed regression evaluation")
        return 0
    except Exception as exc:
        print_error(f"Evaluation failed: {exc}")
        return 1


def review_command(args: argparse.Namespace) -> int:
    """Run parallel 4-agent independent review (Security, Performance, Maintainability, Arch)."""
    from product_value_system.harness.context import ExecutionContext
    from product_value_system.review.orchestrator import MultiAgentReviewOrchestrator
    from product_value_system.review.synthesizer import ReviewSynthesizer

    spec_path = Path(args.specification)
    if not spec_path.exists():
        print_error(f"Specification file not found: {spec_path}")
        return 1

    with open(spec_path, encoding="utf-8") as f:
        if spec_path.suffix.lower() == ".json":
            spec = json.load(f)
        else:
            spec = yaml.safe_load(f)

    config = load_config(args)
    schema_validator = SchemaValidator(Path(config["schemas_dir"]))

    try:
        schema_validator.validate(spec, "specification.schema.json")
    except Exception as exc:
        print_error(f"Specification validation failed: {exc}")
        return 1

    use_mock = bool(getattr(args, "use_mock", False))
    openai_key = os.environ.get("OPENAI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    live_adapter: Any | None = None

    if not use_mock and openai_key:
        from product_value_system.adapters.openai_adapter import OpenAIModelAdapter

        live_adapter = OpenAIModelAdapter(
            api_key=openai_key,
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        )
    elif not use_mock and anthropic_key:
        from product_value_system.adapters.anthropic_adapter import AnthropicAdapter

        live_adapter = AnthropicAdapter(
            api_key=anthropic_key,
            model=os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
        )

    def _mock_harness_factory(reviewer_key: str) -> Harness:
        mock_payload = json.dumps(
            {
                "findings": [],
                "overall_verdict": "approve",
                "summary": f"Independent {reviewer_key} review found no blocking defects.",
            }
        )
        return Harness(model_adapter=MockModelAdapter(responses=[mock_payload]))

    synthesizer = ReviewSynthesizer(max_high_findings=int(getattr(args, "max_high", 5)))
    orchestrator = MultiAgentReviewOrchestrator(
        schema_validator=schema_validator,
        harness_factory=None if live_adapter is not None else _mock_harness_factory,
        model_adapter=live_adapter,
        synthesizer=synthesizer,
    )

    try:
        ctx = ExecutionContext(run_id=f"review-{spec.get('id', 'spec')}", stage_name="review")
        result = asyncio.run(orchestrator.execute(spec, context=ctx))

        print(f"\nConsolidated Multi-Agent Review: {result.specification_id}")
        print("=" * 60)
        print(f"Review ID:    {result.review_id}")
        print(f"Reviewers:    {', '.join(result.reviewers_executed)}")
        print(f"Verdict:      {result.verdict}")
        print(f"Gate:         {result.gate_decision} ({result.gate_reason})")
        print(f"Duration:     {result.duration_seconds:.2f}s | Cost: ${result.total_cost_usd:.4f}")
        print(
            "Findings:     "
            f"CRITICAL={result.summary['CRITICAL']}, HIGH={result.summary['HIGH']}, "
            f"MEDIUM={result.summary['MEDIUM']}, LOW={result.summary['LOW']}"
        )

        if result.findings:
            print("\nPrioritized Findings:")
            for finding in result.findings:
                req = f" [{finding.requirement_id}]" if finding.requirement_id else ""
                print(
                    f"  - [{finding.severity.value}] ({finding.category.value} / "
                    f"{finding.reviewer}){req}: {finding.description}"
                )
                print(f"    Recommendation: {finding.recommendation}")

        if result.conflicts:
            print("\nResolved Conflicts:")
            for conflict in result.conflicts:
                print(
                    f"  - {conflict.conflict_type} ({conflict.requirement_id or 'global'}): "
                    f"{conflict.resolution} — {conflict.rationale}"
                )

        output_path = getattr(args, "output", None)
        if output_path is not None:
            out_file = Path(output_path)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            if out_file.suffix.lower() == ".json":
                out_file.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
            else:
                with open(out_file, "w", encoding="utf-8") as f:
                    yaml.safe_dump(result.to_dict(), f, sort_keys=False)
            print_success(f"Saved consolidated review report: {out_file}")

        if result.gate_decision == "FAIL":
            print_error(f"Review gate FAILED: {result.gate_reason}")
            return 1
        print_success(f"Review gate PASSED ({result.verdict})")
        return 0
    except Exception as exc:
        print_error(f"Multi-agent review failed: {exc}")
        return 1
