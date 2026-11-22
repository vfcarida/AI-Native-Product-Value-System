"""Integration and performance tests for EVO-028 parallel agent execution."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_value_system.agents.agent import AgentResult
from product_value_system.harness.harness import Harness
from product_value_system.orchestrator import (
    AgentDefinition,
    CheckpointManager,
    ConflictResolver,
    ConflictStrategy,
    ParallelExecutor,
    ResourceManager,
    ResourceRequirements,
    RunStatus,
    ValueStreamOrchestrator,
    build_dependency_graph,
)
from product_value_system.policy.engine import PolicyEngine

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_timed_agent(
    name: str,
    delay: float,
    payload_fn: Any,
    *,
    memory_mb: int = 256,
    llm_calls: int = 1,
) -> Any:
    class _TimedAgent:
        def __init__(self) -> None:
            self.name = name
            self.resource_requirements = ResourceRequirements(
                memory_mb=memory_mb,
                llm_calls=llm_calls,
            )

        async def execute(self, inputs: dict[str, Any]) -> Any:
            await asyncio.sleep(delay)
            return payload_fn(inputs)

    return _TimedAgent()


def _make_orchestrator_stage_agent(
    name: str,
    delay: float,
    output: dict[str, Any],
    cost_usd: float = 0.01,
) -> Any:
    """Async agent implementing the 3-arg ValueStreamOrchestrator agent contract."""

    class _StageAgent:
        def __init__(self) -> None:
            self.name = name

        async def execute(
            self,
            _input_artifact: dict[str, Any],
            _harness: Any,
            _context: Any,
        ) -> AgentResult:
            await asyncio.sleep(delay)
            return AgentResult(
                success=True,
                output_artifact=dict(output),
                cost_usd=cost_usd,
                iterations=1,
                duration_seconds=delay,
                reasoning_trace=[],
                tool_calls=[],
                checkpoints=[],
            )

    return _StageAgent()


async def test_multi_review_parallel_execution_and_conflict_merge() -> None:
    """Architecture, Security, Performance, and Maintainability reviews run in parallel."""
    dag = build_dependency_graph(
        [
            AgentDefinition(name="specification", depends_on=[]),
            AgentDefinition(
                name="review_architecture",
                depends_on=["specification"],
                output_type="review",
                priority=2,
            ),
            AgentDefinition(
                name="review_security",
                depends_on=["specification"],
                output_type="review",
                priority=4,
            ),
            AgentDefinition(
                name="review_performance",
                depends_on=["specification"],
                output_type="review",
                priority=3,
            ),
            AgentDefinition(
                name="review_maintainability",
                depends_on=["specification"],
                output_type="review",
                priority=1,
            ),
            AgentDefinition(
                name="synthesis",
                depends_on=[
                    "review_architecture",
                    "review_security",
                    "review_performance",
                    "review_maintainability",
                ],
            ),
        ]
    )

    registry = {
        "specification": _make_timed_agent(
            "specification", 0.02, lambda _i: {"spec_id": "SPEC-1"}
        ),
        "review_architecture": _make_timed_agent(
            "review_architecture",
            0.08,
            lambda i: {
                "verdict": "approve",
                "findings": [f"arch-ok:{i['specification_output']['spec_id']}"],
            },
        ),
        "review_security": _make_timed_agent(
            "review_security",
            0.07,
            lambda _i: {"verdict": "request_changes", "findings": ["add-rate-limit"]},
        ),
        "review_performance": _make_timed_agent(
            "review_performance",
            0.06,
            lambda _i: {"verdict": "approve", "findings": ["index-lookup"]},
        ),
        "review_maintainability": _make_timed_agent(
            "review_maintainability",
            0.05,
            lambda _i: {"verdict": "approve", "findings": ["split-module"]},
        ),
        "synthesis": _make_timed_agent(
            "synthesis",
            0.01,
            lambda i: {
                "reviewers": sorted(i["dependency_outputs"].keys()),
                "verdicts": {
                    k: v["verdict"] for k, v in sorted(i["dependency_outputs"].items())
                },
            },
        ),
    }

    executor = ParallelExecutor(
        conflict_resolver=ConflictResolver(ConflictStrategy.MERGE),
        max_concurrent=5,
    )
    summary = await executor.execute_with_summary(dag, {}, registry)

    assert summary.layers_executed == 3
    assert summary.peak_concurrent_agents == 4
    assert all(r.success for r in summary.results.values())
    assert summary.results["synthesis"].output["reviewers"] == [
        "review_architecture",
        "review_maintainability",
        "review_performance",
        "review_security",
    ]
    assert summary.resolution_report is not None
    merged_review = summary.resolution_report.resolved_outputs["review"]
    assert merged_review["verdict"] == "request_changes"
    assert set(merged_review["findings"]) == {
        "arch-ok:SPEC-1",
        "add-rate-limit",
        "index-lookup",
        "split-module",
    }


async def test_latency_reduction_exceeds_20_percent() -> None:
    """Parallel execution must cut multi-review workflow latency by >=20% vs sequential."""
    dag = build_dependency_graph(
        [
            AgentDefinition(name="discovery", depends_on=[]),
            AgentDefinition(name="spec", depends_on=["discovery"]),
            AgentDefinition(name="arch", depends_on=["spec"]),
            AgentDefinition(name="sec", depends_on=["spec"]),
            AgentDefinition(name="perf", depends_on=["spec"]),
            AgentDefinition(name="maint", depends_on=["spec"]),
            AgentDefinition(name="synth", depends_on=["arch", "sec", "perf", "maint"]),
        ]
    )

    registry = {
        "discovery": _make_timed_agent("discovery", 0.03, lambda _i: {"d": 1}),
        "spec": _make_timed_agent("spec", 0.03, lambda _i: {"s": 1}),
        "arch": _make_timed_agent("arch", 0.10, lambda _i: {"v": "approve"}),
        "sec": _make_timed_agent("sec", 0.09, lambda _i: {"v": "approve"}),
        "perf": _make_timed_agent("perf", 0.08, lambda _i: {"v": "approve"}),
        "maint": _make_timed_agent("maint", 0.07, lambda _i: {"v": "approve"}),
        "synth": _make_timed_agent("synth", 0.02, lambda _i: {"done": True}),
    }

    seq_executor = ParallelExecutor(max_concurrent=5, enabled=False)
    seq_summary = await seq_executor.execute_with_summary(dag, {}, registry)

    par_executor = ParallelExecutor(max_concurrent=5, enabled=True)
    par_summary = await par_executor.execute_with_summary(dag, {}, registry)

    reduction_pct = (
        (seq_summary.total_duration_seconds - par_summary.total_duration_seconds)
        / seq_summary.total_duration_seconds
        * 100.0
    )
    assert par_summary.total_duration_seconds < seq_summary.total_duration_seconds
    assert reduction_pct >= 20.0, f"Expected >=20% latency reduction, got {reduction_pct:.1f}%"
    assert par_summary.speedup >= 1.35


async def test_parallel_and_sequential_results_identical() -> None:
    """Outputs and conflict resolution must be identical between parallel and sequential modes."""
    dag = build_dependency_graph(
        [
            AgentDefinition(name="root", depends_on=[]),
            AgentDefinition(name="b1", depends_on=["root"], output_type="branch", priority=2),
            AgentDefinition(name="b2", depends_on=["root"], output_type="branch", priority=1),
            AgentDefinition(name="b3", depends_on=["root"], output_type="branch", priority=3),
            AgentDefinition(name="join", depends_on=["b1", "b2", "b3"]),
        ]
    )

    registry = {
        "root": _make_timed_agent("root", 0.01, lambda i: {"base": i["seed"]}),
        "b1": _make_timed_agent(
            "b1", 0.04, lambda i: {"items": [f"b1:{i['root_output']['base']}"], "score": 1}
        ),
        "b2": _make_timed_agent(
            "b2", 0.02, lambda i: {"items": [f"b2:{i['root_output']['base']}"], "score": 2}
        ),
        "b3": _make_timed_agent(
            "b3", 0.03, lambda i: {"items": [f"b3:{i['root_output']['base']}"], "score": 3}
        ),
        "join": _make_timed_agent(
            "join",
            0.01,
            lambda i: {
                "sum_scores": (
                    i["b1_output"]["score"] + i["b2_output"]["score"] + i["b3_output"]["score"]
                )
            },
        ),
    }

    seq_summary = await ParallelExecutor(enabled=False).execute_with_summary(
        dag, {"seed": "alpha"}, registry
    )
    par_summary = await ParallelExecutor(enabled=True).execute_with_summary(
        dag, {"seed": "alpha"}, registry
    )

    seq_outputs = {k: v.output for k, v in seq_summary.results.items()}
    par_outputs = {k: v.output for k, v in par_summary.results.items()}
    assert par_outputs == seq_outputs
    assert seq_summary.resolution_report is not None
    assert par_summary.resolution_report is not None
    assert (
        par_summary.resolution_report.resolved_outputs
        == seq_summary.resolution_report.resolved_outputs
    )


async def test_large_graph_12_agents_5_layers() -> None:
    """Execute a 12-agent, 5-layer DAG and validate every upstream/downstream ordering invariant."""
    defs = [
        # Layer 0
        AgentDefinition(name="n0_a", depends_on=[]),
        AgentDefinition(name="n0_b", depends_on=[]),
        # Layer 1
        AgentDefinition(name="n1_a", depends_on=["n0_a"]),
        AgentDefinition(name="n1_b", depends_on=["n0_a", "n0_b"]),
        AgentDefinition(name="n1_c", depends_on=["n0_b"]),
        # Layer 2
        AgentDefinition(name="n2_a", depends_on=["n1_a", "n1_b"]),
        AgentDefinition(name="n2_b", depends_on=["n1_b"]),
        AgentDefinition(name="n2_c", depends_on=["n1_b", "n1_c"]),
        # Layer 3
        AgentDefinition(name="n3_a", depends_on=["n2_a", "n2_b"]),
        AgentDefinition(name="n3_b", depends_on=["n2_b", "n2_c"]),
        AgentDefinition(name="n3_c", depends_on=["n2_c"]),
        # Layer 4
        AgentDefinition(name="n4_final", depends_on=["n3_a", "n3_b", "n3_c"]),
    ]
    dag = build_dependency_graph(defs)
    assert len(dag.get_execution_layers()) == 5

    registry = {
        d.name: _make_timed_agent(
            d.name,
            0.015,
            lambda inp, nm=d.name: {
                "agent": nm,
                "deps_count": len(inp["dependency_outputs"]),
            },
        )
        for d in defs
    }

    executor = ParallelExecutor(max_concurrent=5)
    summary = await executor.execute_with_summary(dag, {}, registry)

    assert summary.layers_executed == 5
    assert len(summary.results) == 12
    assert all(r.success for r in summary.results.values())
    assert summary.results["n4_final"].output == {"agent": "n4_final", "deps_count": 3}

    for agent_def in defs:
        child = summary.results[agent_def.name]
        for dep in agent_def.depends_on:
            parent = summary.results[dep]
            assert child.started_at >= parent.completed_at


async def test_resource_exhaustion_and_throttling() -> None:
    """Heavy parallel layer must stay strictly inside LLM and memory caps without deadlock."""
    defs = [
        AgentDefinition(
            name=f"heavy_{i}",
            depends_on=[],
            resource_requirements=ResourceRequirements(memory_mb=600, llm_calls=1),
        )
        for i in range(8)
    ]
    dag = build_dependency_graph(defs)
    registry = {
        d.name: _make_timed_agent(
            d.name, 0.02, lambda _inp, nm=d.name: {"ok": nm}, memory_mb=600, llm_calls=1
        )
        for d in defs
    }

    rm = ResourceManager(
        max_concurrent_llm_calls=2,
        max_memory_mb=1200,
        llm_rate_limit_per_minute=100,
    )
    executor = ParallelExecutor(resource_manager=rm, max_concurrent=8)
    summary = await executor.execute_with_summary(dag, {}, registry)

    assert all(r.success for r in summary.results.values())
    assert summary.resource_snapshot.peak_llm_calls <= 2
    assert summary.resource_snapshot.peak_memory_mb <= 1200
    assert summary.resource_snapshot.current_llm_calls == 0
    assert summary.resource_snapshot.current_memory_mb == 0
    assert summary.resource_snapshot.total_waits > 0


async def test_value_stream_orchestrator_parallel_end_to_end_vs_sequential(
    tmp_path: Path,
) -> None:
    """ValueStreamOrchestrator runs independent reviews concurrently with >=20% speedup."""
    agents = {
        "discovery": _make_orchestrator_stage_agent(
            "discovery-agent", 0.01, {"id": "out-1", "hypothesis": "Faster checkout"}
        ),
        "specification": _make_orchestrator_stage_agent(
            "specification-agent", 0.01, {"id": "spec-1", "requirements": ["R1"]}
        ),
        "review_architecture": _make_orchestrator_stage_agent(
            "architecture-review-agent", 0.25, {"verdict": "approve", "notes": "clean"}
        ),
        "review_security": _make_orchestrator_stage_agent(
            "security-review-agent", 0.25, {"verdict": "approve", "notes": "secure"}
        ),
        "review_performance": _make_orchestrator_stage_agent(
            "performance-review-agent", 0.25, {"verdict": "approve", "notes": "fast"}
        ),
        "build": _make_orchestrator_stage_agent(
            "build-agent", 0.01, {"files": ["app.py"], "tests_passed": True}
        ),
    }

    problem: dict[str, Any] = {
        "id": "prob-parallel-001",
        "version": 1,
        "created": datetime.now(UTC).isoformat(),
        "statement": "Validate end-to-end parallel review execution",
    }
    policy_engine = PolicyEngine()
    shared_harness = Harness(policy_engine=policy_engine)

    # Sequential run (config override enabled=False)
    seq_orch = ValueStreamOrchestrator(
        agents=agents,
        evaluation_pipeline=None,
        policy_engine=policy_engine,
        checkpoint_manager=CheckpointManager(tmp_path / "cp_seq"),
        artifact_root=tmp_path / "art_seq",
        harness_factory=lambda _stage: shared_harness,
        enable_parallel_execution=False,
    )
    t0 = time.monotonic()
    seq_run = await seq_orch.execute_run(problem)
    seq_elapsed = time.monotonic() - t0
    assert seq_run.status == RunStatus.COMPLETED

    # Parallel run loaded from config/orchestrator.yaml (runs arch + sec + perf concurrently!)
    par_orch = ValueStreamOrchestrator(
        agents=agents,
        evaluation_pipeline=None,
        policy_engine=policy_engine,
        checkpoint_manager=CheckpointManager(tmp_path / "cp_par"),
        artifact_root=tmp_path / "art_par",
        harness_factory=lambda _stage: shared_harness,
        parallel_config=REPO_ROOT / "config" / "orchestrator.yaml",
    )
    t1 = time.monotonic()
    par_run = await par_orch.execute_run(problem)
    par_elapsed = time.monotonic() - t1

    assert par_run.status == RunStatus.COMPLETED
    # Parallel run executes all 10 base stages + review_performance = 11 stages concurrently
    assert len(par_run.stages) == 11
    assert "review_architecture" in par_run.stages
    assert "review_security" in par_run.stages
    assert "review_performance" in par_run.stages
    assert par_elapsed < seq_elapsed
    assert (seq_elapsed - par_elapsed) / seq_elapsed >= 0.20

    assert par_orch.last_parallel_summary is not None
    assert par_orch.last_parallel_summary.peak_concurrent_agents == 3
    assert par_orch.last_parallel_summary.speedup >= 2.0
    parallel_events = [e for e in par_orch.events if e["type"] == "layer.parallel_completed"]
    assert len(parallel_events) == 1
    assert set(parallel_events[0]["data"]["stages"]) == {
        "review_architecture",
        "review_security",
        "review_performance",
    }

    par_dir = Path(par_run.artifact_dir or "")
    assert (par_dir / "architecture_review.yaml").exists()
    assert (par_dir / "security_review.yaml").exists()
    assert (par_dir / "review_performance.yaml").exists()
    assert (par_dir / "build_result.yaml").exists()


async def test_performance_concurrency_limit_strictly_enforced() -> None:
    """Concurrency cap (max_concurrent=3) must never be exceeded under 10-way fan-out."""
    defs = [AgentDefinition(name=f"w_{i}", depends_on=[]) for i in range(10)]
    dag = build_dependency_graph(defs)

    active = {"now": 0, "peak": 0}

    async def tracked_worker(_inputs: dict[str, Any]) -> dict[str, int]:
        active["now"] += 1
        active["peak"] = max(active["peak"], active["now"])
        await asyncio.sleep(0.02)
        active["now"] -= 1
        return {"ok": 1}

    executor = ParallelExecutor(
        resource_manager=ResourceManager(max_concurrent_llm_calls=10, max_memory_mb=4000),
        max_concurrent=3,
    )
    summary = await executor.execute_with_summary(
        dag, {}, {d.name: tracked_worker for d in defs}
    )
    assert active["peak"] == 3
    assert summary.peak_concurrent_agents == 3
    assert all(r.success for r in summary.results.values())


async def test_performance_memory_usage_stays_within_budget() -> None:
    """Mixed memory-profile agents must strictly respect max_memory_mb at every instant."""
    profiles = [250, 400, 600, 350, 500, 300, 450, 250, 600, 300, 400, 200]
    defs = [
        AgentDefinition(
            name=f"mem_{i}",
            depends_on=[],
            resource_requirements=ResourceRequirements(memory_mb=mb, llm_calls=1),
        )
        for i, mb in enumerate(profiles)
    ]
    dag = build_dependency_graph(defs)
    rm = ResourceManager(max_concurrent_llm_calls=10, max_memory_mb=1000)

    observed_peaks: list[int] = []

    def make_worker(mb: int) -> Any:
        async def _work(_inputs: dict[str, Any]) -> dict[str, int]:
            observed_peaks.append(rm.current_memory_mb)
            assert rm.current_memory_mb <= 1000
            await asyncio.sleep(0.015)
            return {"mb": mb}

        return _work

    executor = ParallelExecutor(resource_manager=rm, max_concurrent=12)
    summary = await executor.execute_with_summary(
        dag, {}, {f"mem_{i}": make_worker(mb) for i, mb in enumerate(profiles)}
    )
    assert all(r.success for r in summary.results.values())
    assert max(observed_peaks) <= 1000
    assert rm.peak_memory_mb <= 1000
    assert rm.current_memory_mb == 0
