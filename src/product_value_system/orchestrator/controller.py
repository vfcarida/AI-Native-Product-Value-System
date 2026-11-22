"""
Value stream orchestrator controller.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from product_value_system.agents.agent import AgentResult
from product_value_system.evaluation.pipeline import EvaluationPipeline
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.learning.analytics_adapter import AnalyticsAdapter
from product_value_system.learning.feedback_loop import FeedbackLoop
from product_value_system.learning.measurement_agent import MeasurementAgent
from product_value_system.learning.outcome_tracker import OutcomeTracker
from product_value_system.observability.tracing import set_run_context, trace_span
from product_value_system.orchestrator.checkpoints import CheckpointManager
from product_value_system.orchestrator.conflict_resolver import ConflictResolver
from product_value_system.orchestrator.dependency_graph import (
    AgentDefinition,
    DependencyGraph,
    ResourceRequirements,
)
from product_value_system.orchestrator.parallel_executor import (
    ParallelExecutionSummary,
    ParallelExecutor,
)
from product_value_system.orchestrator.resource_manager import ResourceManager
from product_value_system.orchestrator.state_machine import (
    RunStatus,
    StageResult,
    StageStatus,
    ValueStreamRun,
    ValueStreamStateMachine,
)
from product_value_system.policy.engine import PolicyEngine


def load_orchestrator_config(path: Path | str) -> dict[str, Any]:
    """Load orchestrator YAML configuration (e.g., ``config/orchestrator.yaml``)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return dict(raw) if isinstance(raw, dict) else {}


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
        *,
        analytics_adapter: AnalyticsAdapter | None = None,
        outcome_tracker: OutcomeTracker | None = None,
        feedback_loop: FeedbackLoop | None = None,
        use_multi_agent_review: bool = False,
        review_orchestrator: Any | None = None,
        feature_flag_service: Any | None = None,
        experiment_design_agent: Any | None = None,
        parallel_config: dict[str, Any] | Path | str | None = None,
        enable_parallel_execution: bool | None = None,
        parallel_executor: ParallelExecutor | None = None,
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
            analytics_adapter: Optional analytics adapter enabling the Learn stage
            outcome_tracker: Optional pre-configured OutcomeTracker
            feedback_loop: Optional FeedbackLoop for alerts and Discovery feedback
            use_multi_agent_review: Enable parallel 4-agent independent review synthesis
            review_orchestrator: Optional pre-configured MultiAgentReviewOrchestrator
            feature_flag_service: Optional FeatureFlagService for progressive delivery gating
            experiment_design_agent: Optional ExperimentDesignAgent for A/B experiment blueprints
        """
        self.agents = agents
        self.evaluation_pipeline = evaluation_pipeline
        self.policy_engine = policy_engine
        self.checkpoint_manager = checkpoint_manager
        self.artifact_root = Path(artifact_root)
        self.harness_factory = harness_factory
        self.events: list[dict[str, Any]] = []
        self.use_multi_agent_review = use_multi_agent_review
        self.review_orchestrator = review_orchestrator
        self.feature_flag_service = feature_flag_service
        self.experiment_design_agent = experiment_design_agent or agents.get("experiment_design")

        self.outcome_tracker: OutcomeTracker | None = outcome_tracker or (
            OutcomeTracker(analytics_adapter) if analytics_adapter is not None else None
        )
        self.measurement_agent: Any | None = agents.get("measurement") or (
            MeasurementAgent(self.outcome_tracker)
            if self.outcome_tracker is not None
            else None
        )
        self.feedback_loop: FeedbackLoop | None = feedback_loop or (
            FeedbackLoop(self.outcome_tracker) if self.outcome_tracker is not None else None
        )

        # EVO-028: Parallel execution configuration and engine
        raw_p_cfg: dict[str, Any] = {}
        if isinstance(parallel_config, (Path, str)):
            raw_p_cfg = load_orchestrator_config(parallel_config)
        elif isinstance(parallel_config, dict):
            raw_p_cfg = dict(parallel_config)
        orch_cfg = raw_p_cfg.get("orchestrator", raw_p_cfg)
        pe_cfg = orch_cfg.get("parallel_execution", {}) if isinstance(orch_cfg, dict) else {}

        if enable_parallel_execution is not None:
            self.parallel_enabled = bool(enable_parallel_execution)
        elif "enabled" in pe_cfg:
            self.parallel_enabled = bool(pe_cfg["enabled"])
        else:
            self.parallel_enabled = parallel_executor is not None

        self.resource_manager = (
            parallel_executor.resource_manager
            if parallel_executor is not None
            else ResourceManager(
                max_concurrent_llm_calls=int(pe_cfg.get("max_concurrent_llm_calls", 10)),
                max_memory_mb=int(pe_cfg.get("memory_limit_mb", 2000)),
                llm_rate_limit_per_minute=int(pe_cfg.get("llm_rate_limit_per_minute", 100)),
            )
        )
        self.conflict_resolver = (
            parallel_executor.conflict_resolver
            if parallel_executor is not None
            else ConflictResolver(
                strategy=str(pe_cfg.get("conflict_strategy", "merge")),
            )
        )
        self.parallel_executor = parallel_executor or ParallelExecutor(
            resource_manager=self.resource_manager,
            conflict_resolver=self.conflict_resolver,
            max_concurrent=int(pe_cfg.get("max_concurrent_agents", 5)),
            fallback_to_sequential=bool(pe_cfg.get("fallback_to_sequential", True)),
            enabled=True,
        )
        self.last_parallel_summary: ParallelExecutionSummary | None = None

    def build_dependency_graph(
        self,
        *,
        parallel_build_with_review: bool = False,
    ) -> DependencyGraph:
        """Build the value stream DAG exposing parallelizable review & build stages."""
        dag = DependencyGraph()
        dag.add_agent(AgentDefinition(name="problem_analysis", depends_on=[]))
        dag.add_agent(
            AgentDefinition(name="outcome_hypothesis", depends_on=["problem_analysis"])
        )
        dag.add_agent(AgentDefinition(name="discovery", depends_on=["outcome_hypothesis"]))
        dag.add_agent(AgentDefinition(name="decision_gate", depends_on=["discovery"]))
        dag.add_agent(AgentDefinition(name="specification", depends_on=["decision_gate"]))

        review_stages = ["review_architecture", "review_security"]
        for extra in ("review_performance", "review_maintainability"):
            if extra in self.agents:
                review_stages.append(extra)

        for idx, rev_stage in enumerate(review_stages):
            dag.add_agent(
                AgentDefinition(
                    name=rev_stage,
                    depends_on=["specification"],
                    output_type="review",
                    priority=len(review_stages) - idx,
                    resource_requirements=ResourceRequirements(memory_mb=256, llm_calls=1),
                )
            )

        if parallel_build_with_review:
            dag.add_agent(AgentDefinition(name="build", depends_on=["specification"]))
            dag.add_agent(
                AgentDefinition(name="quality_gate", depends_on=["build", *review_stages])
            )
        else:
            dag.add_agent(AgentDefinition(name="build", depends_on=list(review_stages)))
            dag.add_agent(AgentDefinition(name="quality_gate", depends_on=["build"]))

        dag.add_agent(AgentDefinition(name="release_decision", depends_on=["quality_gate"]))
        dag.validate()
        return dag

    async def execute_parallel_agents(
        self,
        dependency_graph: DependencyGraph,
        initial_inputs: dict[str, Any],
        agent_registry: dict[str, Any] | None = None,
    ) -> ParallelExecutionSummary:
        """Execute an arbitrary agent DAG using this orchestrator's parallel executor."""
        registry = agent_registry if agent_registry is not None else self.agents
        summary = await self.parallel_executor.execute_with_summary(
            dependency_graph=dependency_graph,
            initial_inputs=initial_inputs,
            agent_registry=registry,
        )
        self.last_parallel_summary = summary
        return summary

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

    def _evaluate_flag(
        self,
        flag_name: str,
        run_id: str,
        config: dict[str, Any],
    ) -> Any | None:
        """Evaluate feature flag if configured and emit an audit event."""
        if self.feature_flag_service is None:
            return None
        user_id = config.get("user_id")
        req_ctx = dict(config.get("request_context") or {})
        req_ctx.setdefault("request_id", run_id)
        if "environment" in config and "environment" not in req_ctx:
            req_ctx["environment"] = config["environment"]
        evaluation = self.feature_flag_service.is_enabled(
            flag_name,
            user_id=str(user_id) if user_id is not None else None,
            request_context=req_ctx,
        )
        self._emit_event("feature_flag.evaluated", run_id, evaluation.to_dict())
        return evaluation

    def _is_multi_review_enabled(self, run_id: str, config: dict[str, Any]) -> bool:
        """Determine whether multi-agent review is active via config or feature flag."""
        if "enable_multi_review" in config:
            return bool(config["enable_multi_review"])
        if self.use_multi_agent_review:
            return True
        evaluation = self._evaluate_flag("multi_agent_review", run_id, config)
        return bool(evaluation and evaluation.enabled)

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

        from product_value_system.monitoring.metrics import get_metrics

        metrics = get_metrics()
        metrics.track_active_run(1)

        try:
            kill_eval = self._evaluate_flag("cost_kill_switch", run.id, config)
            if kill_eval is not None and kill_eval.enabled:
                raise OrchestratorError(
                    "Run blocked by ops feature flag 'cost_kill_switch' "
                    f"({kill_eval.reason})"
                )

            use_parallel = bool(
                config.get("enable_parallel_execution", self.parallel_enabled)
            )
            if use_parallel:
                cancelled = await self._execute_run_parallel_layers(
                    run=run,
                    problem=problem,
                    config=config,
                    metrics=metrics,
                )
                if cancelled:
                    return run
            else:
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
                        metrics.record_agent_execution(
                            agent=stage_result.agent_name or next_stage,
                            status="error",
                            cost_usd=stage_result.cost_usd,
                        )
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

                    metrics.record_agent_execution(
                        agent=stage_result.agent_name or next_stage,
                        status="success",
                        cost_usd=stage_result.cost_usd,
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

            outcome_achieved = True
            if config.get("enable_measurement", False):
                run.current_stage = "measure"
                self._emit_event("stage.started", run.id, {"stage": "measure"})
                measure_result, measured_achieved = await self._execute_learn_stage(
                    run=run,
                    config=config,
                )
                run.add_stage_result(measure_result)
                self.checkpoint_manager.save_checkpoint(run)

                if measure_result.status == StageStatus.FAILED:
                    self._emit_event(
                        "stage.failed",
                        run.id,
                        {"stage": "measure", "error": measure_result.error},
                    )
                    run.status = RunStatus.FAILED
                    run.completed_at = datetime.now(UTC)
                    self.checkpoint_manager.save_checkpoint(run)
                    raise OrchestratorError(
                        f"Stage measure failed: {measure_result.error}"
                    )

                self._emit_event("stage.completed", run.id, {"stage": "measure"})
                outcome_achieved = measured_achieved

            run.status = RunStatus.COMPLETED
            run.outcome_achieved = outcome_achieved
            run.completed_at = datetime.now(UTC)
            self.checkpoint_manager.save_checkpoint(run)

            return run

        except Exception:
            run.status = RunStatus.FAILED
            run.completed_at = datetime.now(UTC)
            self.checkpoint_manager.save_checkpoint(run)
            raise
        finally:
            metrics.track_active_run(-1)

    async def _execute_run_parallel_layers(
        self,
        *,
        run: ValueStreamRun,
        problem: dict[str, Any],
        config: dict[str, Any],
        metrics: Any,
    ) -> bool:
        """Execute value stream stages layer-by-layer using ``ParallelExecutor`` (EVO-028).

        Returns True if the run was cancelled at the human approval gate.
        """
        dag = self.build_dependency_graph(
            parallel_build_with_review=bool(config.get("parallel_build_with_review", False))
        )
        layers = dag.get_execution_layers()

        for layer in layers:
            for stage_name in layer.agents:
                self._emit_event("stage.started", run.id, {"stage": stage_name})

            if len(layer.agents) == 1:
                stage_name = layer.agents[0]
                run.current_stage = stage_name
                stage_results = {
                    stage_name: await self._execute_stage(
                        stage_name=stage_name,
                        run=run,
                        problem=problem,
                        config=config,
                    )
                }
            else:
                layer_dag = DependencyGraph()
                stage_runners: dict[str, Any] = {}
                for stage_name in layer.agents:
                    base_def = dag.agents[stage_name]
                    layer_dag.add_agent(
                        AgentDefinition(
                            name=stage_name,
                            depends_on=[],
                            output_type=base_def.output_type,
                            priority=base_def.priority,
                            resource_requirements=base_def.resource_requirements,
                        )
                    )

                    async def _run_stage(_inputs: dict[str, Any], st: str = stage_name) -> Any:
                        return await self._execute_stage(
                            stage_name=st,
                            run=run,
                            problem=problem,
                            config=config,
                        )

                    stage_runners[stage_name] = _run_stage

                summary = await self.parallel_executor.execute_with_summary(
                    dependency_graph=layer_dag,
                    initial_inputs={"problem": problem},
                    agent_registry=stage_runners,
                )
                self.last_parallel_summary = summary
                self._emit_event(
                    "layer.parallel_completed",
                    run.id,
                    {
                        "layer_number": layer.layer_number,
                        "stages": list(layer.agents),
                        "speedup": round(summary.speedup, 4),
                        "peak_concurrent_agents": summary.peak_concurrent_agents,
                    },
                )
                stage_results = {}
                for stage_name in layer.agents:
                    exec_res = summary.results[stage_name]
                    if isinstance(exec_res.output, StageResult):
                        stage_results[stage_name] = exec_res.output
                    else:
                        stage_results[stage_name] = StageResult(
                            stage_name=stage_name,
                            status=StageStatus.FAILED,
                            started_at=datetime.now(UTC),
                            completed_at=datetime.now(UTC),
                            error=str(exec_res.error or "Parallel stage execution failed"),
                        )

            failed_stage: tuple[str, StageResult] | None = None
            for stage_name in layer.agents:
                sr = stage_results[stage_name]
                run.current_stage = stage_name
                run.add_stage_result(sr)
                if sr.status == StageStatus.FAILED:
                    metrics.record_agent_execution(
                        agent=sr.agent_name or stage_name,
                        status="error",
                        cost_usd=sr.cost_usd,
                    )
                    self._emit_event(
                        "stage.failed",
                        run.id,
                        {"stage": stage_name, "error": sr.error},
                    )
                    if failed_stage is None:
                        failed_stage = (stage_name, sr)
                else:
                    metrics.record_agent_execution(
                        agent=sr.agent_name or stage_name,
                        status="success",
                        cost_usd=sr.cost_usd,
                    )
                    self._emit_event("stage.completed", run.id, {"stage": stage_name})

            self.checkpoint_manager.save_checkpoint(run)

            if failed_stage is not None:
                f_name, f_res = failed_stage
                run.status = RunStatus.FAILED
                run.completed_at = datetime.now(UTC)
                self.checkpoint_manager.save_checkpoint(run)
                raise OrchestratorError(f"Stage {f_name} failed: {f_res.error}")

            if "decision_gate" in layer.agents and config.get("require_human_approval"):
                run.status = RunStatus.WAITING_APPROVAL
                self._emit_event("approval.requested", run.id, {"stage": "decision_gate"})
                self.checkpoint_manager.save_checkpoint(run)
                if config.get("human_approved", True) is False:
                    self._emit_event("approval.denied", run.id, {"stage": "decision_gate"})
                    run.status = RunStatus.CANCELLED
                    run.completed_at = datetime.now(UTC)
                    self.checkpoint_manager.save_checkpoint(run)
                    return True
                self._emit_event("approval.granted", run.id, {"stage": "decision_gate"})
                run.status = RunStatus.RUNNING

        return False

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

                discovery_flag = self._evaluate_flag("new_discovery_prompt", run.id, config)
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
                if discovery_flag is not None:
                    stage_result.metadata["new_discovery_prompt"] = discovery_flag.enabled
                if config.get("enable_experiment_design", False):
                    exp_agent = self.experiment_design_agent
                    if exp_agent is None:
                        from product_value_system.agents.experiment_design_agent import (
                            ExperimentDesignAgent,
                        )

                        exp_agent = ExperimentDesignAgent()
                    exp_design, exp_cost = await self._invoke_agent(
                        exp_agent,
                        output_artifact,
                        "experiment_design",
                        run.id,
                    )
                    exp_path = await self._save_artifact(
                        run.artifact_dir,
                        "experiment_design.yaml",
                        exp_design,
                    )
                    stage_result.cost_usd += exp_cost
                    stage_result.metadata["experiment_design_path"] = exp_path
                    stage_result.metadata["experiment_id"] = exp_design.get("id")
                    self._emit_event(
                        "experiment.designed",
                        run.id,
                        {
                            "experiment_id": exp_design.get("id"),
                            "sample_size_per_variant": exp_design.get("sample_size_per_variant"),
                            "duration_days": exp_design.get("duration_days"),
                            "artifact_path": exp_path,
                        },
                    )
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
                use_multi = self._is_multi_review_enabled(run.id, config)
                if use_multi:
                    spec_stage = run.get_stage("specification")
                    if not spec_stage or not spec_stage.artifact_path:
                        raise OrchestratorError("Specification artifact missing")
                    spec = await self._load_artifact(spec_stage.artifact_path)

                    rev_orch = self.review_orchestrator
                    if rev_orch is None:
                        from product_value_system.contracts.validation import SchemaValidator
                        from product_value_system.review.orchestrator import (
                            MultiAgentReviewOrchestrator,
                        )

                        ref_agent = self.agents.get("review_architecture") or self.agents.get(
                            "specification"
                        )
                        validator = getattr(ref_agent, "schema_validator", None) or SchemaValidator(
                            Path(__file__).resolve().parents[3] / "schemas"
                        )
                        rev_orch = MultiAgentReviewOrchestrator(
                            schema_validator=validator,
                            harness_factory=self.harness_factory,
                            policy_engine=self.policy_engine,
                        )

                    review_ctx = ExecutionContext(
                        run_id=run.id,
                        stage_name="multi_agent_review",
                        agent_name="multi-agent-review-orchestrator",
                    )
                    consolidated = await rev_orch.execute(spec, context=review_ctx)
                    artifact_path = await self._save_artifact(
                        run.artifact_dir,
                        "consolidated_review.yaml",
                        consolidated.to_dict(),
                    )
                    self._emit_event(
                        "review.synthesized",
                        run.id,
                        {
                            "review_id": consolidated.review_id,
                            "verdict": consolidated.verdict,
                            "gate_decision": consolidated.gate_decision,
                            "summary": consolidated.summary,
                            "conflicts": len(consolidated.conflicts),
                        },
                    )
                    stage_result.artifact_path = artifact_path
                    stage_result.agent_name = "multi-agent-review-orchestrator"
                    stage_result.cost_usd = consolidated.total_cost_usd
                    stage_result.metadata = {
                        "verdict": consolidated.verdict,
                        "gate_decision": consolidated.gate_decision,
                        "reviewers": consolidated.reviewers_executed,
                    }
                    if consolidated.gate_decision == "FAIL":
                        raise OrchestratorError(
                            f"Multi-agent review gate failed: {consolidated.gate_reason}"
                        )
                    stage_result.status = StageStatus.COMPLETED
                else:
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
                arch_stage = run.get_stage("review_architecture")
                ran_multi = bool(
                    arch_stage and arch_stage.agent_name == "multi-agent-review-orchestrator"
                )
                if ran_multi or bool(
                    config.get("enable_multi_review", self.use_multi_agent_review)
                ):
                    # Security perspective already executed concurrently in multi-agent review
                    stage_result.artifact_path = arch_stage.artifact_path if arch_stage else None
                    stage_result.agent_name = "security-review-agent (parallel)"
                    stage_result.status = StageStatus.COMPLETED
                else:
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

            elif stage_name in {"review_performance", "review_maintainability"}:
                agent = self.agents.get(stage_name)
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
                        f"{stage_name}.yaml",
                        output_artifact,
                    )
                    stage_result.artifact_path = artifact_path
                    stage_result.agent_name = getattr(agent, "name", f"{stage_name}-agent")
                    stage_result.cost_usd = cost_usd
                    if output_artifact.get("verdict") == "reject":
                        raise OrchestratorError(f"{stage_name} rejected specification")
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

    async def _execute_learn_stage(
        self,
        run: ValueStreamRun,
        config: dict[str, Any],
    ) -> tuple[StageResult, bool]:
        """
        Execute the optional Learn stage (post-launch outcome measurement & feedback loop).

        Returns:
            Tuple of ``(StageResult, outcome_achieved)``
        """
        stage_result = StageResult(
            stage_name="measure",
            status=StageStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        assert run.artifact_dir is not None

        try:
            outcome_stage = run.get_stage("outcome_hypothesis")
            if not outcome_stage or not outcome_stage.artifact_path:
                raise OrchestratorError("Outcome hypothesis artifact missing for Learn stage")
            outcome = await self._load_artifact(outcome_stage.artifact_path)
            outcome_id = str(outcome.get("id") or f"outcome-{run.problem_id}")

            window_days = int(config.get("measurement_window_days", 30))
            baseline_window_days = int(config.get("baseline_window_days", 30))
            raw_deploy = config.get("deployment_date")
            if isinstance(raw_deploy, datetime):
                deploy_date = raw_deploy
            elif isinstance(raw_deploy, str):
                deploy_date = datetime.fromisoformat(raw_deploy)
            else:
                deploy_date = datetime.now(UTC) - timedelta(days=window_days)

            stage_costs = {name: st.cost_usd for name, st in run.stages.items()}
            if self.outcome_tracker is not None:
                self.outcome_tracker.register_deployment(
                    outcome_id=outcome_id,
                    deployment_date=deploy_date,
                    outcome_hypothesis=outcome,
                    development_cost_usd=run.total_cost_usd,
                    stage_costs=stage_costs,
                    ongoing_cost_per_month_usd=config.get("ongoing_cost_per_month_usd"),
                )

            if config.get("defer_measurement", False):
                schedule_artifact = {
                    "outcome_id": outcome_id,
                    "status": "SCHEDULED",
                    "deployment_date": deploy_date.isoformat(),
                    "measurement_window_days": window_days,
                    "scheduled_for": (deploy_date + timedelta(days=window_days)).isoformat(),
                }
                artifact_path = await self._save_artifact(
                    run.artifact_dir,
                    "measurement_schedule.yaml",
                    schedule_artifact,
                )
                stage_result.artifact_path = artifact_path
                stage_result.agent_name = "measurement-scheduler"
                stage_result.status = StageStatus.COMPLETED
                stage_result.completed_at = datetime.now(UTC)
                self._emit_event("measurement.scheduled", run.id, schedule_artifact)
                return stage_result, True

            if self.measurement_agent is None:
                raise OrchestratorError(
                    "Learn stage enabled but no measurement_agent or analytics_adapter configured"
                )

            measurement_input: dict[str, Any] = {
                "outcome_id": outcome_id,
                "measurement_window_days": window_days,
                "baseline_window_days": baseline_window_days,
                "generate_llm_insights": bool(config.get("measurement_llm_insights", False)),
            }
            output_artifact, cost_usd = await self._invoke_agent(
                self.measurement_agent,
                measurement_input,
                "measure",
                run.id,
            )

            artifact_path = await self._save_artifact(
                run.artifact_dir,
                "measurement.yaml",
                output_artifact,
            )

            # Enrich outcome.yaml with post-launch measurement record
            outcome["measurement"] = output_artifact
            await self._save_artifact(run.artifact_dir, "outcome.yaml", outcome)

            alerts_count = 0
            if (
                self.feedback_loop is not None
                and self.outcome_tracker is not None
                and outcome_id in self.outcome_tracker.measurements
            ):
                measurement_obj = self.outcome_tracker.measurements[outcome_id]
                alerts = await self.feedback_loop.evaluate_measurement(measurement_obj)
                alerts_count = len(alerts)
                self.feedback_loop.record_feedback(measurement_obj)
                await self._save_artifact(
                    run.artifact_dir,
                    "discovery_feedback.yaml",
                    self.feedback_loop.build_discovery_context(),
                )

            self._emit_event(
                "learning.captured",
                run.id,
                {
                    "outcome_id": outcome_id,
                    "status": output_artifact.get("status"),
                    "alerts_count": alerts_count,
                },
            )

            stage_result.artifact_path = artifact_path
            stage_result.agent_name = getattr(self.measurement_agent, "name", "measurement-agent")
            stage_result.cost_usd = cost_usd
            stage_result.metadata = {
                "outcome_id": outcome_id,
                "measurement_status": output_artifact.get("status"),
                "alerts_count": alerts_count,
            }
            stage_result.status = StageStatus.COMPLETED
            stage_result.completed_at = datetime.now(UTC)

            achieved = output_artifact.get("status") == "ACHIEVED"
            return stage_result, achieved

        except Exception as e:
            stage_result.status = StageStatus.FAILED
            stage_result.error = str(e)
            stage_result.completed_at = datetime.now(UTC)
            return stage_result, False

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
