"""EVO-012 tests: Trajectory capture, judges, TrajectoryEvaluator, and golden trajectories."""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.adapters.model_adapter import (
    ModelResponse,
    Usage,
)
from product_value_system.adapters.model_adapter import ToolCall as AdapterToolCall
from product_value_system.agents.agent import AgentResult
from product_value_system.agents.base_agent import BaseAgent
from product_value_system.cli.main import main as cli_main
from product_value_system.evaluation.golden_dataset_loader import GoldenDatasetLoader
from product_value_system.evaluation.pipeline import EvaluationPipeline
from product_value_system.evaluation.regression_eval import (
    RegressionEvaluator,
    RubricComplianceJudge,
)
from product_value_system.evaluation.trajectory_capture import (
    AgentTrajectory,
    TrajectoryCapture,
    TrajectoryStep,
    load_golden_trajectories,
    load_golden_trajectory,
)
from product_value_system.evaluation.trajectory_eval import (
    TrajectoryEvalResult,
    TrajectoryEvaluator,
)
from product_value_system.evaluation.trajectory_judges import (
    TrajectoryEfficiencyJudge,
    TrajectoryReasoningJudge,
    TrajectorySafetyJudge,
    TrajectoryToolUseJudge,
)
from product_value_system.harness.budget import Budget
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.guardrails import Guardrails
from product_value_system.harness.harness import Harness
from product_value_system.security.secret_detection import SecretDetector

pytestmark = pytest.mark.eval

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_TRAJ_DIR = REPO_ROOT / "golden_datasets" / "trajectories"


class _EchoBaseAgent(BaseAgent):
    """Minimal BaseAgent subclass exercising automatic trajectory start/finalize."""

    def __init__(self) -> None:
        super().__init__(
            name="echo-agent",
            version="0.1.0",
            capabilities=["echo"],
            required_tools=[],
            input_schema="https://pvs.ai/schemas/problem.schema.json",
            output_schema="https://pvs.ai/schemas/outcome.schema.json",
        )

    async def _execute_impl(
        self,
        input_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> AgentResult:
        resp = await harness.invoke_model(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Thought: Analyze user request carefully.\nProduce structured summary."
                    ),
                }
            ]
        )
        return AgentResult(
            success=True,
            output_artifact={"id": "out-echo", "summary": resp.content},
            iterations=1,
            cost_usd=float(harness.budget.current_cost_usd),
            duration_seconds=0.0,
            reasoning_trace=["Echoed response"],
            tool_calls=[],
            checkpoints=[],
        )


class _DummyTool:
    """Minimal Tool implementation for testing Harness.execute_tool trajectory logging."""

    name = "knowledge.query"
    description = "Query synthetic knowledge base"
    parameters: dict[str, Any] = {"type": "object", "properties": {"query": {"type": "string"}}}

    async def execute(self, **kwargs: Any) -> str:
        return f"Results for {kwargs.get('query', '')}"


def _make_step(
    idx: int,
    thought: str = "Analyze problem step",
    action: dict[str, Any] | None = None,
    observation: str | None = None,
    policy_checked: bool = True,
    policy_decision: str | None = "ALLOW",
) -> TrajectoryStep:
    return TrajectoryStep(
        step_index=idx,
        timestamp=datetime.now(UTC),
        thought=thought,
        action=action,
        observation=observation,
        latency_ms=100.0,
        tokens_used=120,
        cost_usd=0.002,
        policy_checked=policy_checked,
        policy_decision=policy_decision,
    )


def test_trajectory_capture_starts_logs_persists_and_redacts(tmp_path: Path) -> None:
    """TrajectoryCapture logs steps, persists JSON, and redacts secret patterns."""
    capture = TrajectoryCapture(enabled=True, redact_secrets=True, storage_dir=tmp_path)
    capture.start_trajectory("discovery", {"problem": "test"}, sample_id="discovery-001")

    fake_key = "sk-" + ("a1b2c3d4" * 6)
    capture.log_step(
        thought=f"Inspecting config with leaked key {fake_key}",
        action={"tool": "knowledge.query", "parameters": {"query": "payment alerts"}},
        observation=f"Observed {fake_key} in legacy log",
        latency_ms=125.0,
        tokens_used=150,
        cost_usd=0.0015,
        policy_checked=True,
        policy_decision="ALLOW",
    )

    trajectory = capture.finalize_trajectory({"outcome": "ok"}, success=True)
    assert trajectory is not None
    assert trajectory.total_steps == 1
    assert trajectory.total_tokens == 150
    assert fake_key not in trajectory.steps[0].thought
    assert fake_key not in (trajectory.steps[0].observation or "")
    assert (tmp_path / f"{trajectory.trajectory_id}.json").exists()

    reloaded = AgentTrajectory.load(tmp_path / f"{trajectory.trajectory_id}.json")
    assert reloaded.trajectory_id == trajectory.trajectory_id
    assert reloaded.sample_id == "discovery-001"
    assert len(reloaded.steps) == 1


def test_trajectory_disabled_and_sampling_modes() -> None:
    """Disabled capture returns None; sample_rate=0 skips recording."""
    disabled = TrajectoryCapture(enabled=False)
    disabled.start_trajectory("discovery", {})
    disabled.log_step(thought="should not be recorded")
    assert disabled.finalize_trajectory({}, success=True) is None

    zero_sample = TrajectoryCapture(enabled=True, sample_rate=0.0, seed=42)
    zero_sample.start_trajectory("discovery", {})
    zero_sample.log_step(thought="sampled out")
    assert zero_sample.finalize_trajectory({}, success=True) is None


@pytest.mark.asyncio
async def test_harness_and_base_agent_automatic_trajectory_capture() -> None:
    """Harness.invoke_model, Harness.execute_tool, and BaseAgent automatically log trajectories."""
    capture = TrajectoryCapture(enabled=True)

    class _ToolCallModelAdapter(MockModelAdapter):
        async def invoke(self, messages: Any, **kwargs: Any) -> ModelResponse:
            return ModelResponse(
                content="Thought: Need to gather domain facts.\nSummary ready.",
                model="mock-tool",
                usage=Usage(input_tokens=40, output_tokens=20, total_tokens=60),
                tool_calls=[
                    AdapterToolCall(
                        id="tc-1",
                        name="knowledge.query",
                        arguments={"query": "fees"},
                    )
                ],
                finish_reason="tool_calls",
            )

    ctx = ExecutionContext(run_id="run-t1", stage_name="discovery", agent_name="discovery")
    harness = Harness(
        context=ctx,
        model_adapter=_ToolCallModelAdapter(),
        trajectory_capture=capture,
    )
    harness.tools.register(_DummyTool())

    agent = _EchoBaseAgent()
    result = await agent.execute({"id": "problem-echo"}, harness, ctx)

    assert result.success is True
    assert result.trajectory is not None
    assert result.trajectory.total_steps == 1
    assert result.trajectory.steps[0].action == {
        "tool": "knowledge.query",
        "parameters": {"query": "fees"},
    }
    assert "gather domain facts" in result.trajectory.steps[0].thought

    # Also test execute_tool observation attachment on an open trajectory
    capture.start_trajectory("discovery", {"id": "p2"})
    capture.log_step(
        thought="Search knowledge base",
        action={"tool": "knowledge.query", "parameters": {"query": "fees"}},
    )
    tc = await harness.execute_tool("knowledge.query", {"query": "fees"})
    traj2 = capture.finalize_trajectory({"ok": True})
    assert traj2 is not None
    assert traj2.steps[0].observation is not None
    assert tc.tool_name == "knowledge.query"


@pytest.mark.asyncio
async def test_trajectory_evaluator_detects_inefficient_redundant_and_unsafe_runs() -> None:
    """TrajectoryEvaluator catches redundant tool loops and policy/unsafe violations."""
    # Example failure from EVO-012 Section A: redundant searches + unsafe shell attempt
    bad_traj = AgentTrajectory(
        trajectory_id="bad-001",
        agent_name="specification",
        input_artifact={"problem": {"statement": "Generate notification service spec"}},
        output_artifact={"id": "spec-valid"},
    )
    for i in range(1, 9):
        bad_traj.add_step(
            _make_step(
                i,
                thought="Searching knowledge base again",
                action={"tool": "knowledge.query", "parameters": {"query": "notifications"}},
                observation="Same search results",
            )
        )
    bad_traj.add_step(
        _make_step(
            9,
            thought="Attempt shell command despite policy forbid",
            action={"tool": "shell.exec", "parameters": {"cmd": "rm -rf /tmp/cache"}},
            observation="DENIED by policy",
            policy_checked=True,
            policy_decision="DENY",
        )
    )

    evaluator = TrajectoryEvaluator()
    result = await evaluator.evaluate(bad_traj)

    assert isinstance(result, TrajectoryEvalResult)
    assert result.passed is False
    assert result.policy_violations == 1
    assert result.unsafe_actions_attempted == 1
    assert result.redundant_tool_calls == 7
    assert "policy violation" in result.rationale.lower()


@pytest.mark.asyncio
async def test_structural_similarity_identical_and_divergent() -> None:
    """Identical action sequences score 1.0; completely different sequences score 0.0."""
    traj_a = AgentTrajectory("a", "discovery", {}, {})
    traj_a.add_step(_make_step(1, action={"tool": "knowledge.query"}))
    traj_a.add_step(_make_step(2, action={"tool": "schema.validate"}))

    traj_b = AgentTrajectory("b", "discovery", {}, {})
    traj_b.add_step(_make_step(1, action={"tool": "knowledge.query"}))
    traj_b.add_step(_make_step(2, action={"tool": "schema.validate"}))

    traj_c = AgentTrajectory("c", "discovery", {}, {})
    traj_c.add_step(_make_step(1, action={"tool": "unrelated.a"}))
    traj_c.add_step(_make_step(2, action={"tool": "unrelated.b"}))

    evaluator = TrajectoryEvaluator()
    assert evaluator._structural_similarity(traj_a, traj_b) == 1.0
    assert evaluator._structural_similarity(traj_a, traj_c) == 0.0


@pytest.mark.asyncio
async def test_error_recovery_scoring() -> None:
    """Recovered trajectory scores higher on error recovery than unrecovered failed run."""
    recovered = AgentTrajectory("rec", "build", {}, {}, success=True)
    recovered.add_step(
        _make_step(1, action={"tool": "test.run"}, observation="AssertionError failed")
    )
    recovered.add_step(_make_step(2, action={"tool": "test.run"}, observation="All tests passed"))

    failed = AgentTrajectory("fail", "build", {}, {}, success=False)
    failed.add_step(_make_step(1, action={"tool": "test.run"}, observation="AssertionError failed"))

    evaluator = TrajectoryEvaluator()
    assert evaluator._error_recovery(recovered) == pytest.approx(0.85)
    assert evaluator._error_recovery(failed) == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_trajectory_evaluator_with_llm_judge_and_dict_input() -> None:
    """TrajectoryEvaluator accepts both LLM ModelAdapter judge and deserialized dict input."""
    llm_judge = MockModelAdapter(
        responses=[
            json.dumps(
                {
                    "logical_coherence": 9,
                    "understanding": 9,
                    "completeness": 8,
                    "clarity": 9,
                    "overall": 8.75,
                }
            )
        ]
    )
    golden = load_golden_trajectory("discovery-001", root_path=GOLDEN_TRAJ_DIR)
    assert golden is not None

    evaluator = TrajectoryEvaluator(llm_judge=llm_judge)
    result = await evaluator.evaluate(golden.to_dict(), golden_trajectory=golden)

    assert result.passed is True
    assert result.structural_similarity == 1.0
    assert result.reasoning_quality == pytest.approx(0.875)
    assert result.policy_violations == 0
    assert result.overall_score >= 0.85


@pytest.mark.asyncio
async def test_trajectory_judges_compose_inside_evaluation_pipeline() -> None:
    """All 4 specialized trajectory judges conform to Judge protocol inside EvaluationPipeline."""
    golden = load_golden_trajectory("specification-001", root_path=GOLDEN_TRAJ_DIR)
    assert golden is not None

    judges = [
        TrajectoryEfficiencyJudge(),
        TrajectoryToolUseJudge(),
        TrajectorySafetyJudge(),
        TrajectoryReasoningJudge(),
    ]
    assert all(j.judge_type == "trajectory" for j in judges)

    pipeline = EvaluationPipeline(judges=judges, passing_threshold=0.7)
    eval_res = await pipeline.evaluate(
        {"id": golden.trajectory_id, "trajectory": golden},
        context={"artifact_type": "trajectory", "golden_trajectory": golden},
    )
    assert eval_res.passed is True
    assert eval_res.overall_score >= 0.85


def test_25_curated_golden_trajectories_valid_and_secret_free() -> None:
    """golden_datasets/trajectories/ contains 25 valid, secret-free exemplar trajectories."""
    trajectories = load_golden_trajectories(GOLDEN_TRAJ_DIR)
    assert len(trajectories) == 25

    counts: dict[str, int] = {}
    for traj in trajectories:
        counts[traj.agent_name] = counts.get(traj.agent_name, 0) + 1
        assert traj.total_steps >= 2
        assert traj.success is True
        assert traj.sample_id is not None

    assert counts == {
        "brownfield": 4,
        "build": 5,
        "discovery": 6,
        "review": 4,
        "specification": 6,
    }

    detector = SecretDetector()
    for fp in sorted(GOLDEN_TRAJ_DIR.glob("*.jsonl")):
        assert detector.find_secrets(fp.read_text(encoding="utf-8")) == []


@pytest.mark.asyncio
async def test_regression_evaluator_with_trajectory_evaluation(tmp_path: Path) -> None:
    """RegressionEvaluator integrates trajectory capture and scoring alongside output eval."""
    loader = GoldenDatasetLoader(REPO_ROOT / "golden_datasets")
    dataset = loader.load("discovery", version=1, categories=["happy-path"]).sample_subset(
        2, seed=1
    )

    class _GoodDiscoveryStub:
        name = "discovery-agent"

        async def execute(
            self,
            input_artifact: dict[str, Any],
            harness: Harness,
            context: Any,
        ) -> dict[str, Any]:
            await harness.invoke_model(
                messages=[{"role": "user", "content": "Thought: Define measurable outcome metric."}]
            )
            return {
                "id": "outcome-stub",
                "hypothesis": (
                    "Improving notification delivery will raise delivery rate from 60% to 95% "
                    "without increasing latency."
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
                        {"metric": "latency_ms", "threshold": 3000.0, "direction": "below"}
                    ],
                },
                "evidence_requirements": [
                    {"type": "quantitative", "description": "Analytics report", "mandatory": True}
                ],
            }

    pipeline = EvaluationPipeline(judges=[RubricComplianceJudge()], passing_threshold=0.7)
    evaluator = RegressionEvaluator(
        agent=_GoodDiscoveryStub(),
        harness=Harness(model_adapter=MockModelAdapter(responses=["ok"])),
        eval_pipeline=pipeline,
        harness_factory=lambda _s: Harness(model_adapter=MockModelAdapter(responses=["ok"])),
        trajectory_evaluator=TrajectoryEvaluator(),
        golden_trajectories_dir=GOLDEN_TRAJ_DIR,
    )

    result = await evaluator.evaluate(dataset, parallel=True)
    assert result.pass_rate == 1.0
    assert result.mean_trajectory_score is not None
    assert result.mean_trajectory_score >= 0.7
    assert result.total_policy_violations == 0
    assert "Trajectory:" in result.summary()

    report_path = result.save_json(tmp_path / "traj_eval_report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mean_trajectory_score"] is not None
    assert report["sample_results"][0]["trajectory_eval"]["num_steps"] >= 1


def test_cli_pvs_eval_with_trajectories(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """pvs eval discovery --with-trajectories evaluates both outputs and trajectories."""
    rc = cli_main(
        [
            "eval",
            "discovery",
            "--fast",
            "--subset-size",
            "4",
            "--with-trajectories",
            "--threshold",
            "0.75",
            "--baseline-dir",
            str(tmp_path / "baselines"),
            "--use-mock",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "Trajectory:" in out
    assert "Violations:   0" in out


def test_cli_pvs_run_capture_trajectory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pvs run --capture-trajectory --output saves a valid AgentTrajectory JSON file."""
    monkeypatch.setenv("PVS_DB_PATH", str(tmp_path / "pvs.db"))
    monkeypatch.setenv("PVS_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    traj_out = tmp_path / "trajectory.json"

    rc = cli_main(
        [
            "run",
            str(REPO_ROOT / "examples" / "greenfield" / "problem_payment_flow.yaml"),
            "--capture-trajectory",
            "--output",
            str(traj_out),
        ]
    )
    assert rc == 0
    assert traj_out.exists()
    loaded = AgentTrajectory.load(traj_out)
    assert loaded.total_steps >= 4
    assert loaded.success is True


@pytest.mark.asyncio
async def test_trajectory_capture_low_overhead_when_disabled() -> None:
    """Disabled TrajectoryCapture adds minimal overhead across repeated invocations."""
    harness = Harness(
        model_adapter=MockModelAdapter(responses=["ok"] * 30),
        guardrails=Guardrails(max_iterations=100),
        trajectory_capture=TrajectoryCapture(enabled=False),
    )
    harness.budget.max_iterations = 100
    t0 = time.perf_counter()
    for _ in range(25):
        await harness.invoke_model([{"role": "user", "content": "Short status check."}])
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert harness.trajectory_capture.get_trajectory() is None
    assert elapsed_ms < 2000.0


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="Requires OPENAI_API_KEY")
@pytest.mark.asyncio
async def test_real_trajectory_capture_discovery_agent() -> None:
    """Live integration test: capture and evaluate a real DiscoveryAgent trajectory."""
    from product_value_system.adapters.openai_adapter import OpenAIModelAdapter
    from product_value_system.agents.discovery_agent import DiscoveryAgent
    from product_value_system.contracts.validation import SchemaValidator

    validator = SchemaValidator(REPO_ROOT / "schemas")
    adapter = OpenAIModelAdapter(
        api_key=os.environ["OPENAI_API_KEY"],
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    )
    agent = DiscoveryAgent(validator)
    capture = TrajectoryCapture(enabled=True)
    problem = {
        "id": "problem-live-traj",
        "version": 1,
        "created": "2026-11-19T10:00:00Z",
        "statement": (
            "Mobile users miss instant payment confirmations and submit duplicate transfers."
        ),
        "context": {
            "user_segment": "Mobile banking users",
            "business_context": "Reduce duplicate payment support tickets",
        },
        "constraints": {"synthetic_only": True, "no_pii": True},
    }
    capture.start_trajectory(agent.name, problem)
    harness = Harness(
        model_adapter=adapter,
        budget=Budget(max_usd=0.5),
        trajectory_capture=capture,
    )
    ctx = ExecutionContext(run_id="live-traj", stage_name="discovery", agent_name=agent.name)
    out = await agent.execute(problem, harness, ctx)
    traj = capture.finalize_trajectory(out, success=True)
    assert traj is not None and traj.total_steps >= 1
    eval_res = await TrajectoryEvaluator().evaluate(traj)
    assert eval_res.policy_violations == 0
