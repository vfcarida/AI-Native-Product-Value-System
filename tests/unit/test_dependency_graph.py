"""Unit tests for EVO-028 DependencyGraph, ParallelExecutor, ResourceManager & ConflictResolver."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from product_value_system.orchestrator import (
    AgentDefinition,
    CheckpointManager,
    ConflictResolver,
    ConflictStrategy,
    DependencyGraph,
    ParallelExecutor,
    ResourceLimitError,
    ResourceManager,
    ResourceRequirements,
    build_dependency_graph,
    load_orchestrator_config,
)
from product_value_system.orchestrator.value_stream_orchestrator import ValueStreamOrchestrator
from product_value_system.security.hybrid_detector import HybridSecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_simple_chain_produces_three_layers() -> None:
    dag = build_dependency_graph(
        [
            AgentDefinition(name="A", depends_on=[]),
            AgentDefinition(name="B", depends_on=["A"]),
            AgentDefinition(name="C", depends_on=["B"]),
        ]
    )
    layers = dag.get_execution_layers()
    assert len(layers) == 3
    assert [layer.agents for layer in layers] == [["A"], ["B"], ["C"]]
    assert layers[0].dependencies == []
    assert layers[1].dependencies == ["A"]
    assert layers[2].dependencies == ["B"]


def test_diamond_graph_groups_parallel_middle_layer() -> None:
    dag = build_dependency_graph(
        [
            AgentDefinition(name="A", depends_on=[]),
            AgentDefinition(name="B", depends_on=["A"]),
            AgentDefinition(name="C", depends_on=["A"]),
            AgentDefinition(name="D", depends_on=["B", "C"]),
        ]
    )
    layers = dag.get_execution_layers()
    assert len(layers) == 3
    assert layers[0].agents == ["A"]
    assert layers[1].agents == ["B", "C"]
    assert layers[2].agents == ["D"]
    assert layers[2].dependencies == ["B", "C"]


def test_cycle_detection_and_missing_dependency_raise_error() -> None:
    cyclic = DependencyGraph()
    cyclic.add_agent(AgentDefinition(name="A", depends_on=["C"]))
    cyclic.add_agent(AgentDefinition(name="B", depends_on=["A"]))
    cyclic.add_agent(AgentDefinition(name="C", depends_on=["B"]))

    with pytest.raises(ValueError, match="cycles"):
        cyclic.validate()
    with pytest.raises(ValueError, match="cycles"):
        cyclic.get_execution_layers()

    dangling = DependencyGraph()
    dangling.add_agent(AgentDefinition(name="B", depends_on=["NON_EXISTENT"]))
    with pytest.raises(ValueError, match="unregistered agents"):
        dangling.validate()


def test_empty_graph_returns_no_layers_and_valid_mermaid() -> None:
    dag = DependencyGraph()
    dag.validate()
    assert dag.get_execution_layers() == []
    assert dag.get_critical_path() == []
    assert dag.visualize() == "graph TD"
    est = dag.estimate_speedup({})
    assert est["speedup"] == 1.0


def test_can_run_parallel_respects_dependencies_and_constraints() -> None:
    dag = build_dependency_graph(
        [
            AgentDefinition(name="spec", depends_on=[]),
            AgentDefinition(
                name="arch",
                depends_on=["spec"],
                can_run_parallel_with=["sec"],
            ),
            AgentDefinition(
                name="sec",
                depends_on=["spec"],
                can_run_parallel_with=["arch"],
            ),
            AgentDefinition(
                name="exclusive",
                depends_on=["spec"],
                cannot_run_parallel_with=["arch", "sec"],
            ),
            AgentDefinition(name="build", depends_on=["arch", "sec", "exclusive"]),
        ]
    )
    assert dag.can_run_parallel("arch", "sec") is True
    assert dag.can_run_parallel("arch", "arch") is False
    assert dag.can_run_parallel("spec", "arch") is False
    assert dag.can_run_parallel("spec", "build") is False
    assert dag.can_run_parallel("arch", "exclusive") is False
    assert dag.can_run_parallel("sec", "exclusive") is False

    # Exclusive peer must be split into a separate sub-layer from [arch, sec]
    layers = dag.get_execution_layers()
    assert [layer.agents for layer in layers] == [
        ["spec"],
        ["arch", "sec"],
        ["exclusive"],
        ["build"],
    ]


def test_critical_path_speedup_and_mermaid_visualization() -> None:
    dag = build_dependency_graph(
        [
            AgentDefinition(name="discovery", depends_on=[]),
            AgentDefinition(name="spec", depends_on=["discovery"]),
            AgentDefinition(name="arch_review", depends_on=["spec"]),
            AgentDefinition(name="sec_review", depends_on=["spec"]),
            AgentDefinition(name="perf_review", depends_on=["spec"]),
            AgentDefinition(
                name="synth",
                depends_on=["arch_review", "sec_review", "perf_review"],
            ),
        ]
    )
    durations = {
        "discovery": 30.0,
        "spec": 45.0,
        "arch_review": 30.0,
        "sec_review": 25.0,
        "perf_review": 20.0,
        "synth": 10.0,
    }
    assert dag.get_critical_path(durations) == ["discovery", "spec", "arch_review", "synth"]

    est = dag.estimate_speedup(durations)
    assert est["sequential_seconds"] == pytest.approx(160.0)
    assert est["parallel_seconds"] == pytest.approx(115.0)
    assert est["speedup"] == pytest.approx(160.0 / 115.0, rel=1e-3)
    assert est["latency_reduction_pct"] > 25.0

    mermaid = dag.visualize()
    assert mermaid.startswith("graph TD")
    assert "spec --> arch_review" in mermaid
    assert "spec --> sec_review" in mermaid
    assert "arch_review --> synth" in mermaid


async def test_parallel_executor_respects_layer_dependencies() -> None:
    dag = build_dependency_graph(
        [
            AgentDefinition(name="A", depends_on=[]),
            AgentDefinition(name="B", depends_on=["A"]),
            AgentDefinition(name="C", depends_on=["A"]),
            AgentDefinition(name="D", depends_on=["B", "C"]),
        ]
    )

    async def run_a(inputs: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.02)
        return {"val": inputs["seed"] + 1}

    async def run_b(inputs: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.02)
        return {"val": inputs["A_output"]["val"] * 10}

    async def run_c(inputs: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.02)
        return {"val": inputs["A_output"]["val"] * 100}

    async def run_d(inputs: dict[str, Any]) -> dict[str, Any]:
        return {"total": inputs["B_output"]["val"] + inputs["C_output"]["val"]}

    executor = ParallelExecutor(max_concurrent=4)
    results = await executor.execute(
        dag,
        {"seed": 2},
        {"A": run_a, "B": run_b, "C": run_c, "D": run_d},
    )
    assert all(r.success for r in results.values())
    assert results["D"].output == {"total": 330}
    assert results["B"].started_at >= results["A"].completed_at
    assert results["C"].started_at >= results["A"].completed_at
    assert results["D"].started_at >= results["B"].completed_at
    assert results["D"].started_at >= results["C"].completed_at


async def test_parallel_executor_runs_layer_agents_concurrently() -> None:
    dag = build_dependency_graph(
        [
            AgentDefinition(name="r1", depends_on=[]),
            AgentDefinition(name="r2", depends_on=[]),
            AgentDefinition(name="r3", depends_on=[]),
        ]
    )

    async def worker(_inputs: dict[str, Any]) -> dict[str, str]:
        await asyncio.sleep(0.06)
        return {"status": "ok"}

    executor = ParallelExecutor(max_concurrent=5)
    summary = await executor.execute_with_summary(
        dag,
        {},
        {"r1": worker, "r2": worker, "r3": worker},
    )
    assert summary.peak_concurrent_agents == 3
    assert summary.total_duration_seconds < summary.sequential_duration_seconds * 0.7
    assert summary.speedup > 1.5


async def test_parallel_executor_isolates_agent_exception() -> None:
    dag = build_dependency_graph(
        [
            AgentDefinition(name="ok_1", depends_on=[]),
            AgentDefinition(name="boom", depends_on=[]),
            AgentDefinition(name="ok_2", depends_on=[]),
            AgentDefinition(name="downstream", depends_on=["ok_1", "boom", "ok_2"]),
        ]
    )

    async def ok_worker(_inputs: dict[str, Any]) -> dict[str, str]:
        await asyncio.sleep(0.01)
        return {"status": "passed"}

    async def failing_worker(_inputs: dict[str, Any]) -> dict[str, str]:
        await asyncio.sleep(0.005)
        raise RuntimeError("Simulated LLM failure")

    async def downstream_worker(inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "received": sorted(inputs["dependency_outputs"].keys()),
            "missing": inputs.get("missing_dependencies", []),
        }

    executor = ParallelExecutor(max_concurrent=4)
    results = await executor.execute(
        dag,
        {},
        {
            "ok_1": ok_worker,
            "boom": failing_worker,
            "ok_2": ok_worker,
            "downstream": downstream_worker,
        },
    )
    assert results["ok_1"].success is True
    assert results["ok_2"].success is True
    assert results["boom"].success is False
    assert isinstance(results["boom"].error, RuntimeError)
    assert results["downstream"].success is True
    assert results["downstream"].output == {
        "received": ["ok_1", "ok_2"],
        "missing": ["boom"],
    }


async def test_resource_manager_enforces_llm_and_memory_limits_without_deadlock() -> None:
    rm = ResourceManager(
        max_concurrent_llm_calls=2,
        max_memory_mb=1000,
        llm_rate_limit_per_minute=100,
    )
    req = ResourceRequirements(memory_mb=500, llm_calls=1)

    async def task() -> None:
        acquired = await rm.acquire(req)
        try:
            assert rm.current_llm_calls <= 2
            assert rm.current_memory_mb <= 1000
            await asyncio.sleep(0.02)
        finally:
            await rm.release(acquired)

    await asyncio.gather(*(task() for _ in range(6)))
    snap = rm.get_snapshot()
    assert snap.current_llm_calls == 0
    assert snap.current_memory_mb == 0
    assert snap.peak_llm_calls <= 2
    assert snap.peak_memory_mb <= 1000
    assert snap.total_acquisitions == 6
    assert snap.total_waits > 0


async def test_resource_manager_rejects_impossible_requirement() -> None:
    rm = ResourceManager(max_concurrent_llm_calls=2, max_memory_mb=1000)
    with pytest.raises(ResourceLimitError, match="max_memory_mb"):
        await rm.acquire(ResourceRequirements(memory_mb=1500, llm_calls=1))
    with pytest.raises(ResourceLimitError, match="max_concurrent_llm_calls"):
        await rm.acquire(ResourceRequirements(memory_mb=100, llm_calls=5))
    assert rm.current_memory_mb == 0
    assert rm.current_llm_calls == 0


async def test_parallel_executor_fallback_to_sequential_on_orchestration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dag = build_dependency_graph(
        [
            AgentDefinition(name="a", depends_on=[]),
            AgentDefinition(name="b", depends_on=["a"]),
        ]
    )

    executor = ParallelExecutor(max_concurrent=2, fallback_to_sequential=True)

    async def broken_parallel(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Simulated parallel scheduler crash")

    monkeypatch.setattr(executor, "_execute_parallel_layers", broken_parallel)

    summary = await executor.execute_with_summary(
        dag,
        {"x": 10},
        {
            "a": lambda inp: {"v": inp["x"] + 5},
            "b": lambda inp: {"v": inp["a_output"]["v"] * 2},
        },
    )
    assert summary.used_sequential_fallback is True
    assert summary.results["b"].success is True
    assert summary.results["b"].output == {"v": 30}


def test_conflict_resolver_merge_priority_and_manual_strategies() -> None:
    outputs = {
        "review_architecture": {
            "verdict": "approve",
            "findings": ["Use async queue"],
            "score": 0.9,
        },
        "review_security": {
            "verdict": "reject",
            "findings": ["Missing input sanitization", "Use async queue"],
            "score": 0.4,
        },
    }
    otypes = {"review_architecture": "review", "review_security": "review"}
    prios = {"review_architecture": 1, "review_security": 5}

    # MERGE: deduplicates lists, takes conservative verdict 'reject', priority for other scalars
    merge_res = ConflictResolver(ConflictStrategy.MERGE).resolve(
        outputs, output_types=otypes, priorities=prios
    )
    merged = merge_res.resolved_outputs["review"]
    assert merged["verdict"] == "reject"
    assert merged["score"] == 0.4
    assert merged["findings"] == ["Missing input sanitization", "Use async queue"]
    assert len(merge_res.conflicts) == 2
    assert merge_res.requires_manual_review is False

    # PRIORITY: selects full output of highest-priority agent
    prio_res = ConflictResolver(ConflictStrategy.PRIORITY).resolve(
        outputs, output_types=otypes, priorities=prios
    )
    assert prio_res.resolved_outputs["review"] == outputs["review_security"]
    assert len(prio_res.conflicts) == 1

    # MANUAL: flags manual review and preserves all candidates
    manual_res = ConflictResolver(ConflictStrategy.MANUAL).resolve(
        outputs, output_types=otypes, priorities=prios
    )
    assert manual_res.requires_manual_review is True
    assert manual_res.resolved_outputs["review"]["requires_manual_review"] is True
    assert set(manual_res.resolved_outputs["review"]["candidates"].keys()) == {
        "review_architecture",
        "review_security",
    }


def test_orchestrator_yaml_config_and_zero_secrets() -> None:
    cfg_path = REPO_ROOT / "config" / "orchestrator.yaml"
    assert cfg_path.exists()
    cfg = load_orchestrator_config(cfg_path)
    pe = cfg["orchestrator"]["parallel_execution"]
    assert pe["enabled"] is True
    assert pe["max_concurrent_agents"] == 5
    assert pe["max_concurrent_llm_calls"] == 10
    assert pe["llm_rate_limit_per_minute"] == 100
    assert pe["memory_limit_mb"] == 2000
    assert pe["conflict_strategy"] == "merge"
    assert pe["fallback_to_sequential"] is True

    detector = HybridSecretDetector()
    for rel in ("config/orchestrator.yaml", "docs/parallel-execution.md"):
        findings = detector.detect((REPO_ROOT / rel).read_text(encoding="utf-8"))
        assert findings == [], f"Unexpected secret findings in {rel}: {findings}"


def test_value_stream_orchestrator_facade_and_dag_structure(tmp_path: Path) -> None:
    orch = ValueStreamOrchestrator(
        agents={
            "review_performance": object(),
            "review_maintainability": object(),
        },
        evaluation_pipeline=None,
        policy_engine=None,
        checkpoint_manager=CheckpointManager(tmp_path / "cp"),
        artifact_root=tmp_path / "art",
        parallel_config=REPO_ROOT / "config" / "orchestrator.yaml",
    )
    assert orch.parallel_enabled is True
    dag = orch.build_dependency_graph()
    layers = dag.get_execution_layers()
    review_layer = next(
        layer for layer in layers if "review_architecture" in layer.agents
    )
    assert review_layer.agents == [
        "review_architecture",
        "review_maintainability",
        "review_performance",
        "review_security",
    ]
    assert "specification --> review_architecture" in dag.visualize()
    assert "specification --> review_security" in dag.visualize()
