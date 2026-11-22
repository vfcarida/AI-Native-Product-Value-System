#!/usr/bin/env python3
"""
Showcase 3: Parallel Agent Execution & Non-Punitive SPACE/DORA DevX ($0 API Cost)

Demonstrates EVO-028 (DAG Parallel Execution) and EVO-032 (SPACE & DORA DevX
metrics with strict aggregate_only privacy guarantees).

Run:
    python examples/showcases/parallel_devx_demo.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from prometheus_client import CollectorRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from product_value_system.metrics import DevXTracker, SPACECalculator  # noqa: E402
from product_value_system.orchestrator import (  # noqa: E402
    AgentDefinition,
    ParallelExecutor,
    build_dependency_graph,
)


async def run_demo() -> None:
    print("=== Showcase 3: Parallel DAG Orchestration & Non-Punitive SPACE/DORA ===\n")

    dag = build_dependency_graph(
        [
            AgentDefinition(name="specification", depends_on=[]),
            AgentDefinition(name="review_architecture", depends_on=["specification"]),
            AgentDefinition(name="review_security", depends_on=["specification"]),
            AgentDefinition(name="review_performance", depends_on=["specification"]),
            AgentDefinition(name="review_maintainability", depends_on=["specification"]),
            AgentDefinition(
                name="release_gate",
                depends_on=[
                    "review_architecture",
                    "review_security",
                    "review_performance",
                    "review_maintainability",
                ],
            ),
        ]
    )

    reg = CollectorRegistry()
    tracker = DevXTracker.from_yaml(REPO_ROOT / "config" / "metrics.yaml", registry=reg)
    base_time = datetime.now(UTC)

    async def _make_agent(stage: str) -> Any:
        async def _run(_inputs: dict[str, Any]) -> dict[str, Any]:
            tracker.start_task(f"demo-{stage}", stage=stage, agent=stage, timestamp=base_time)
            await asyncio.sleep(0.03)
            tracker.complete_task(
                f"demo-{stage}",
                stage=stage,
                success=True,
                timestamp=base_time + timedelta(seconds=120),
            )
            tracker.record_ai_usage(stage, accepted=True, cost_usd=0.005)
            return {"stage": stage, "verdict": "approve"}

        return _run

    registry = {name: await _make_agent(name) for name in dag.agents}
    executor = ParallelExecutor(max_concurrent=5)
    summary = await executor.execute_with_summary(
        dependency_graph=dag,
        initial_inputs={"problem": "demo"},
        agent_registry=registry,
    )

    tracker.record_satisfaction("tool_satisfaction", 4.6)
    tracker.record_deployment(success=True)
    calc = SPACECalculator(tracker=tracker, weekly_task_target=10.0)
    space = await calc.compute_space_scores()
    dora = tracker.get_dora_metrics().to_dict()

    print(
        f"Parallel DAG   : {len(dag.agents)} stages, peak_concurrent="
        f"{summary.peak_concurrent_agents}, speedup={summary.speedup:.2f}x"
    )
    print(
        f"Privacy Mode   : aggregate_only={tracker.aggregate_only}, "
        f"anonymize={tracker.anonymize}"
    )
    print(f"SPACE Overall  : {space['overall']:.2f} (satisfaction={space['satisfaction']:.2f})")
    print(f"DORA Tier      : {dora['performance_tier']}")


if __name__ == "__main__":
    asyncio.run(run_demo())
