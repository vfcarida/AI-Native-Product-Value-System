"""
Value stream orchestrator controller.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from product_value_system.agents.agent import AgentResult
from product_value_system.evaluation.pipeline import EvaluationPipeline
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.tracing import set_run_context, trace_span
from product_value_system.orchestrator.checkpoints import CheckpointManager
from product_value_system.orchestrator.state_machine import (
    RunStatus,
    StageResult,
    StageStatus,
    ValueStreamRun,
    ValueStreamStateMachine,
)
from product_value_system.policy.engine import PolicyEngine


class OrchestratorError(Exception):
    """Orchestrator errors."""


class ValueStreamOrchestrator:
    """
    Orchestrates complete value stream runs.
    """

    def __init__(
        self,
        agents: dict[str, Any],
        evaluation_pipeline: EvaluationPipeline | None,
        policy_engine: PolicyEngine | None,
        checkpoint_manager: CheckpointManager,
        artifact_root: Path | str,
        harness_factory: Any | None = None,
    ) -> None:
        """
        Initialize orchestrator.

        Args:
            agents: Dictionary of agent name -> agent instance
            evaluation_pipeline: Evaluation pipeline for quality gates
            policy_engine: Policy engine for authorization
            checkpoint_manager: Checkpoint manager for recovery
            artifact_root: Root directory for artifacts
            harness_factory: Optional callable(stage_name) -> Harness
        """
        self.agents = agents
        self.evaluation_pipeline = evaluation_pipeline
        self.policy_engine = policy_engine
        self.checkpoint_manager = checkpoint_manager
        self.artifact_root = Path(artifact_root)
        self.harness_factory = harness_factory
        self.events: list[dict[str, Any]] = []

    def _emit_event(
        self,
        event_type: str,
        run_id: str,
        data: dict[str, Any],
    ) -> None:
        """Emit an event conforming to event_envelope.schema.json."""
        self.events.append(
            {
                "id": str(uuid4()),
                "version": "1.0",
                "timestamp": datetime.now(UTC).isoformat(),
                "source": "value-stream-orchestrator",
                "type": event_type,
                "run_id": run_id,
                "data": data,
            }
        )

    def _get_harness(self, stage_name: str, run_id: str, agent_name: str) -> Harness:
        """Build or retrieve harness for an agent execution."""
        if callable(self.harness_factory):
            harness: Harness = self.harness_factory(stage_name)
            return harness
        ctx = ExecutionContext(
            run_id=run_id,
            stage_name=stage_name,
            agent_name=agent_name,
        )
        return Harness(context=ctx, policy_engine=self.policy_engine)

    @trace_span("orchestrator.execute_run")
    async def execute_run(
        self,
        problem: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> ValueStreamRun:
        """
        Execute complete value stream run.

        Args:
            problem: Problem statement (validated against schema)
            config: Optional configuration (human approval thresholds, etc.)

        Returns:
            ValueStreamRun with results

        Raises:
            OrchestratorError: If run fails
        """
        config = config or {}

        run = ValueStreamRun(
            problem_id=str(problem["id"]),
            started_at=datetime.now(UTC),
            artifact_dir=str(self.artifact_root / "runs" / str(problem["id"])),
        )
        set_run_context(run.id)

        assert run.artifact_dir is not None
        Path(run.artifact_dir).mkdir(parents=True, exist_ok=True)

        state_machine = ValueStreamStateMachine(run)

        run.status = RunStatus.RUNNING
        self.checkpoint_manager.save_checkpoint(run)

        try:
            while True:
                next_stage = state_machine.get_next_stage()
                if not next_stage:
                    break

                run.current_stage = next_stage
                self._emit_event("stage.started", run.id, {"stage": next_stage})

                stage_result = await self._execute_stage(
                    stage_name=next_stage,
                    run=run,
                    problem=problem,
                    config=config,
                )

                run.add_stage_result(stage_result)
                self.checkpoint_manager.save_checkpoint(run)

                if stage_result.status == StageStatus.FAILED:
                    self._emit_event(
                        "stage.failed",
                        run.id,
                        {"stage": next_stage, "error": stage_result.error},
                    )
                    run.status = RunStatus.FAILED
                    run.completed_at = datetime.now(UTC)
                    self.checkpoint_manager.save_checkpoint(run)
                    raise OrchestratorError(
                        f"Stage {next_stage} failed: {stage_result.error}"
                    )

                self._emit_event("stage.completed", run.id, {"stage": next_stage})

                if next_stage == "decision_gate" and config.get("require_human_approval"):
                    run.status = RunStatus.WAITING_APPROVAL
                    self._emit_event(
                        "approval.requested", run.id, {"stage": next_stage}
                    )
                    self.checkpoint_manager.save_checkpoint(run)
                    if config.get("human_approved", True) is False:
                        self._emit_event(
                            "approval.denied", run.id, {"stage": next_stage}
                        )
                        run.status = RunStatus.CANCELLED
                        run.completed_at = datetime.now(UTC)
                        self.checkpoint_manager.save_checkpoint(run)
                        return run
                    self._emit_event("approval.granted", run.id, {"stage": next_stage})
                    run.status = RunStatus.RUNNING

            run.status = RunStatus.COMPLETED
            run.outcome_achieved = True
            run.completed_at = datetime.now(UTC)
            self.checkpoint_manager.save_checkpoint(run)

            return run

        except Exception:
            run.status = RunStatus.FAILED
            run.completed_at = datetime.now(UTC)
            self.checkpoint_manager.save_checkpoint(run)
            raise

    async def _invoke_agent(
        self,
        agent: Any,
        input_artifact: dict[str, Any],
        stage_name: str,
        run_id: str,
    ) -> tuple[dict[str, Any], float]:
        """Invoke agent and normalize return value (AgentResult or dict)."""
        agent_name = getattr(agent, "name", stage_name)
        harness = self._get_harness(stage_name, run_id, agent_name)
        context = ExecutionContext(
            run_id=run_id,
            stage_name=stage_name,
            agent_name=agent_name,
        )
        raw = await agent.execute(input_artifact, harness, context)
        if isinstance(raw, AgentResult):
            if not raw.success:
                raise OrchestratorError(raw.error or f"Agent {agent_name} failed")
            return raw.output_artifact, raw.cost_usd
        if isinstance(raw, dict):
            return raw, float(harness.budget.current_cost_usd)
        raise OrchestratorError(f"Unexpected agent output type: {type(raw)}")

    async def _execute_stage(
        self,
        stage_name: str,
        run: ValueStreamRun,
        problem: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        """Execute a single stage."""
        stage_result = StageResult(
            stage_name=stage_name,
            status=StageStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        assert run.artifact_dir is not None

        try:
            if stage_name == "problem_analysis":
                artifact_path = await self._save_artifact(
                    run.artifact_dir,
                    "problem.yaml",
                    problem,
                )
                stage_result.artifact_path = artifact_path
                stage_result.status = StageStatus.COMPLETED

            elif stage_name == "outcome_hypothesis":
                agent = self.agents.get("discovery")
                if not agent:
                    raise OrchestratorError("Discovery agent not found")

                output_artifact, cost_usd = await self._invoke_agent(
                    agent, problem, stage_name, run.id
                )

                artifact_path = await self._save_artifact(
                    run.artifact_dir,
                    "outcome.yaml",
                    output_artifact,
                )

                stage_result.artifact_path = artifact_path
                stage_result.agent_name = getattr(agent, "name", "discovery-agent")
                stage_result.cost_usd = cost_usd
                stage_result.status = StageStatus.COMPLETED

            elif stage_name == "specification":
                agent = self.agents.get("specification")
                if not agent:
                    raise OrchestratorError("Specification agent not found")

                outcome_stage = run.get_stage("outcome_hypothesis")
                if not outcome_stage or not outcome_stage.artifact_path:
                    raise OrchestratorError("Outcome hypothesis artifact missing")
                outcome = await self._load_artifact(outcome_stage.artifact_path)

                output_artifact, cost_usd = await self._invoke_agent(
                    agent, outcome, stage_name, run.id
                )

                artifact_path = await self._save_artifact(
                    run.artifact_dir,
                    "specification.yaml",
                    output_artifact,
                )

                stage_result.artifact_path = artifact_path
                stage_result.agent_name = getattr(agent, "name", "specification-agent")
                stage_result.cost_usd = cost_usd
                stage_result.status = StageStatus.COMPLETED

            elif stage_name == "review_architecture":
                agent = self.agents.get("review_architecture") or self.agents.get(
                    "architecture_review"
                )
                if agent:
                    spec_stage = run.get_stage("specification")
                    if not spec_stage or not spec_stage.artifact_path:
                        raise OrchestratorError("Specification artifact missing")
                    spec = await self._load_artifact(spec_stage.artifact_path)
                    output_artifact, cost_usd = await self._invoke_agent(
                        agent, spec, stage_name, run.id
                    )
                    artifact_path = await self._save_artifact(
                        run.artifact_dir,
                        "architecture_review.yaml",
                        output_artifact,
                    )
                    stage_result.artifact_path = artifact_path
                    stage_result.agent_name = getattr(
                        agent, "name", "architecture-review-agent"
                    )
                    stage_result.cost_usd = cost_usd
                    if output_artifact.get("verdict") == "reject":
                        raise OrchestratorError("Architecture review rejected specification")
                stage_result.status = StageStatus.COMPLETED

            elif stage_name == "review_security":
                agent = self.agents.get("review_security") or self.agents.get(
                    "security_review"
                )
                if agent:
                    spec_stage = run.get_stage("specification")
                    if not spec_stage or not spec_stage.artifact_path:
                        raise OrchestratorError("Specification artifact missing")
                    spec = await self._load_artifact(spec_stage.artifact_path)
                    output_artifact, cost_usd = await self._invoke_agent(
                        agent, spec, stage_name, run.id
                    )
                    artifact_path = await self._save_artifact(
                        run.artifact_dir,
                        "security_review.yaml",
                        output_artifact,
                    )
                    stage_result.artifact_path = artifact_path
                    stage_result.agent_name = getattr(
                        agent, "name", "security-review-agent"
                    )
                    stage_result.cost_usd = cost_usd
                    if output_artifact.get("verdict") == "reject":
                        raise OrchestratorError("Security review rejected specification")
                stage_result.status = StageStatus.COMPLETED

            elif stage_name == "build":
                agent = self.agents.get("build")
                if agent:
                    spec_stage = run.get_stage("specification")
                    if not spec_stage or not spec_stage.artifact_path:
                        raise OrchestratorError("Specification artifact missing")
                    spec = await self._load_artifact(spec_stage.artifact_path)
                    output_artifact, cost_usd = await self._invoke_agent(
                        agent, spec, stage_name, run.id
                    )
                    artifact_path = await self._save_artifact(
                        run.artifact_dir,
                        "build_result.yaml",
                        output_artifact,
                    )
                    stage_result.artifact_path = artifact_path
                    stage_result.agent_name = getattr(agent, "name", "build-agent")
                    stage_result.cost_usd = cost_usd
                stage_result.status = StageStatus.COMPLETED

            elif stage_name == "quality_gate":
                if self.evaluation_pipeline is not None:
                    spec_stage = run.get_stage("specification")
                    if not spec_stage or not spec_stage.artifact_path:
                        raise OrchestratorError("Specification artifact missing")
                    spec = await self._load_artifact(spec_stage.artifact_path)

                    eval_result = await self.evaluation_pipeline.evaluate(
                        artifact=spec,
                        context={"artifact_type": "specification"},
                    )

                    if not eval_result.passed:
                        stage_result.status = StageStatus.FAILED
                        stage_result.error = (
                            f"Quality gate failed: {eval_result.failed_judges}"
                        )
                    else:
                        stage_result.status = StageStatus.COMPLETED
                else:
                    stage_result.status = StageStatus.COMPLETED

            else:
                stage_result.status = StageStatus.COMPLETED

            stage_result.completed_at = datetime.now(UTC)
            return stage_result

        except Exception as e:
            stage_result.status = StageStatus.FAILED
            stage_result.error = str(e)
            stage_result.completed_at = datetime.now(UTC)
            return stage_result

    async def _save_artifact(
        self,
        artifact_dir: str,
        filename: str,
        artifact: Any,
    ) -> str:
        """Save artifact to disk."""
        artifact_path = Path(artifact_dir) / filename

        with open(artifact_path, "w", encoding="utf-8") as f:
            if filename.endswith((".yaml", ".yml")) or isinstance(artifact, dict):
                yaml.safe_dump(artifact, f, sort_keys=False)
            else:
                f.write(str(artifact))

        return str(artifact_path)

    async def _load_artifact(self, artifact_path: str) -> dict[str, Any]:
        """Load artifact from disk."""
        with open(artifact_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                return loaded
            return {"content": str(loaded)}

    async def resume_run(self, run_id: str) -> ValueStreamRun:
        """
        Resume interrupted run from checkpoint.

        Args:
            run_id: Run ID to resume

        Returns:
            Resumed ValueStreamRun
        """
        run = self.checkpoint_manager.load_checkpoint(run_id)
        if not run or not run.artifact_dir:
            raise OrchestratorError(f"No checkpoint found for run {run_id}")

        problem_path = Path(run.artifact_dir) / "problem.yaml"
        with open(problem_path, encoding="utf-8") as f:
            problem: dict[str, Any] = yaml.safe_load(f)

        resumed: ValueStreamRun = await self.execute_run(problem, config={})
        return resumed
