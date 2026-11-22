"""
Asyncio parallel agent execution engine with topological layer scheduling,
resource throttling, exception isolation, conflict resolution, and sequential
fallback (EVO-028).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass
from typing import Any

from product_value_system.orchestrator.conflict_resolver import (
    ConflictResolver,
    ResolutionReport,
)
from product_value_system.orchestrator.dependency_graph import (
    DependencyGraph,
    ExecutionLayer,
    ResourceRequirements,
)
from product_value_system.orchestrator.resource_manager import (
    ResourceManager,
    ResourceSnapshot,
)

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result returned for a single agent within a DAG execution."""

    agent_name: str
    output: Any
    success: bool
    error: Exception | None = None
    duration_seconds: float = 0.0
    layer_number: int = 0
    started_at: float = 0.0
    completed_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "success": self.success,
            "error": str(self.error) if self.error is not None else None,
            "duration_seconds": round(self.duration_seconds, 6),
            "layer_number": self.layer_number,
        }


@dataclass
class ParallelExecutionSummary:
    """Complete execution report with latency speedup, concurrency, and conflict audit."""

    results: dict[str, ExecutionResult]
    layers_executed: int
    total_duration_seconds: float
    sequential_duration_seconds: float
    speedup: float
    used_sequential_fallback: bool
    peak_concurrent_agents: int
    resource_snapshot: ResourceSnapshot
    resolution_report: ResolutionReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": {k: v.to_dict() for k, v in self.results.items()},
            "layers_executed": self.layers_executed,
            "total_duration_seconds": round(self.total_duration_seconds, 6),
            "sequential_duration_seconds": round(self.sequential_duration_seconds, 6),
            "speedup": round(self.speedup, 4),
            "used_sequential_fallback": self.used_sequential_fallback,
            "peak_concurrent_agents": self.peak_concurrent_agents,
            "resource_snapshot": self.resource_snapshot.to_dict(),
            "conflicts": (
                len(self.resolution_report.conflicts)
                if self.resolution_report is not None
                else 0
            ),
        }


class ParallelExecutor:
    """
    Execute agents concurrently per topological layer of a ``DependencyGraph``.

    Guarantees:
    - All upstream dependencies finish before any downstream agent starts.
    - Concurrency never exceeds ``max_concurrent`` or ``ResourceManager`` bounds.
    - An exception in one agent is isolated and never cancels peer agents.
    - Identical per-agent dependency input shaping in parallel and sequential modes.
    """

    def __init__(
        self,
        resource_manager: ResourceManager | None = None,
        conflict_resolver: ConflictResolver | None = None,
        max_concurrent: int = 5,
        *,
        fallback_to_sequential: bool = True,
        enabled: bool = True,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.resource_manager = resource_manager or ResourceManager()
        self.conflict_resolver = conflict_resolver or ConflictResolver()
        self.max_concurrent = int(max_concurrent)
        self.fallback_to_sequential = fallback_to_sequential
        self.enabled = enabled
        self.last_summary: ParallelExecutionSummary | None = None

    async def execute(
        self,
        dependency_graph: DependencyGraph,
        initial_inputs: dict[str, Any],
        agent_registry: dict[str, Any],
    ) -> dict[str, ExecutionResult]:
        """Execute all agents in ``dependency_graph`` and return per-agent results."""
        summary = await self.execute_with_summary(
            dependency_graph=dependency_graph,
            initial_inputs=initial_inputs,
            agent_registry=agent_registry,
        )
        return summary.results

    async def execute_with_summary(
        self,
        dependency_graph: DependencyGraph,
        initial_inputs: dict[str, Any],
        agent_registry: dict[str, Any],
    ) -> ParallelExecutionSummary:
        """Execute DAG and return rich latency, concurrency, and conflict telemetry."""
        layers = dependency_graph.get_execution_layers()
        missing_agents = [
            name for layer in layers for name in layer.agents if name not in agent_registry
        ]
        if missing_agents:
            raise KeyError(f"Agents missing from agent_registry: {sorted(missing_agents)}")

        start_wall = time.monotonic()
        used_fallback = False
        tracker = {"current": 0, "peak": 0}

        if not self.enabled:
            results = await self._execute_sequential_layers(
                dependency_graph, layers, initial_inputs, agent_registry, tracker
            )
        else:
            try:
                results = await self._execute_parallel_layers(
                    dependency_graph, layers, initial_inputs, agent_registry, tracker
                )
            except Exception as exc:
                if not self.fallback_to_sequential:
                    raise
                logger.warning(
                    "Parallel execution infrastructure error (%s); falling back to sequential.",
                    exc,
                )
                used_fallback = True
                results = await self._execute_sequential_layers(
                    dependency_graph, layers, initial_inputs, agent_registry, tracker
                )

        total_wall = time.monotonic() - start_wall
        seq_sum = sum(r.duration_seconds for r in results.values())
        speedup = (seq_sum / total_wall) if total_wall > 0 and seq_sum > 0 else 1.0

        successful_outputs = {
            name: res.output for name, res in results.items() if res.success
        }
        output_types = {
            name: dependency_graph.agents[name].output_type
            for name in successful_outputs
            if name in dependency_graph.agents
        }
        priorities = {
            name: dependency_graph.agents[name].priority
            for name in successful_outputs
            if name in dependency_graph.agents
        }
        resolution_report = self.conflict_resolver.resolve(
            successful_outputs,
            output_types=output_types,
            priorities=priorities,
        )

        summary = ParallelExecutionSummary(
            results=results,
            layers_executed=len(layers),
            total_duration_seconds=total_wall,
            sequential_duration_seconds=seq_sum,
            speedup=speedup,
            used_sequential_fallback=used_fallback,
            peak_concurrent_agents=tracker["peak"],
            resource_snapshot=self.resource_manager.get_snapshot(),
            resolution_report=resolution_report,
        )
        self.last_summary = summary
        return summary

    async def _execute_parallel_layers(
        self,
        dependency_graph: DependencyGraph,
        layers: list[ExecutionLayer],
        initial_inputs: dict[str, Any],
        agent_registry: dict[str, Any],
        tracker: dict[str, int],
    ) -> dict[str, ExecutionResult]:
        results: dict[str, ExecutionResult] = {}
        logger.info("Executing %d layers with max_concurrent=%d", len(layers), self.max_concurrent)

        for layer in layers:
            logger.info(
                "Layer %d: launching %d agent(s) concurrently: %s",
                layer.layer_number,
                len(layer.agents),
                layer.agents,
            )
            layer_inputs = self._prepare_layer_inputs(
                dependency_graph, layer, initial_inputs, results
            )
            layer_results = await self._execute_layer(
                dependency_graph=dependency_graph,
                layer=layer,
                layer_inputs=layer_inputs,
                agent_registry=agent_registry,
                tracker=tracker,
            )
            results.update(layer_results)

            failed = [r.agent_name for r in layer_results.values() if not r.success]
            if failed:
                logger.error(
                    "Layer %d had %d failure(s): %s",
                    layer.layer_number,
                    len(failed),
                    failed,
                )

        return results

    async def _execute_sequential_layers(
        self,
        dependency_graph: DependencyGraph,
        layers: list[ExecutionLayer],
        initial_inputs: dict[str, Any],
        agent_registry: dict[str, Any],
        tracker: dict[str, int],
    ) -> dict[str, ExecutionResult]:
        results: dict[str, ExecutionResult] = {}
        for layer in layers:
            layer_inputs = self._prepare_layer_inputs(
                dependency_graph, layer, initial_inputs, results
            )
            for agent_name in layer.agents:
                res = await self._execute_agent_with_resources(
                    agent=agent_registry[agent_name],
                    agent_name=agent_name,
                    inputs=layer_inputs[agent_name],
                    layer_number=layer.layer_number,
                    declared_req=dependency_graph.agents[agent_name].resource_requirements,
                    tracker=tracker,
                )
                results[agent_name] = res
        return results

    def _prepare_layer_inputs(
        self,
        dependency_graph: DependencyGraph,
        layer: ExecutionLayer,
        initial_inputs: dict[str, Any],
        previous_results: dict[str, ExecutionResult],
    ) -> dict[str, dict[str, Any]]:
        """Build isolated deterministic input dicts for each agent in ``layer``."""
        layer_inputs: dict[str, dict[str, Any]] = {}
        for agent_name in layer.agents:
            agent_inputs: dict[str, Any] = dict(initial_inputs)
            direct_deps = dependency_graph.get_direct_dependencies(agent_name)
            dep_outputs: dict[str, Any] = {}
            missing_deps: list[str] = []
            for dep_name in direct_deps:
                prev = previous_results.get(dep_name)
                if prev is not None and prev.success:
                    agent_inputs[f"{dep_name}_output"] = prev.output
                    dep_outputs[dep_name] = prev.output
                else:
                    missing_deps.append(dep_name)
            agent_inputs["dependency_outputs"] = dep_outputs
            if missing_deps:
                agent_inputs["missing_dependencies"] = missing_deps
            layer_inputs[agent_name] = agent_inputs
        return layer_inputs

    async def _execute_layer(
        self,
        *,
        dependency_graph: DependencyGraph,
        layer: ExecutionLayer,
        layer_inputs: dict[str, dict[str, Any]],
        agent_registry: dict[str, Any],
        tracker: dict[str, int],
    ) -> dict[str, ExecutionResult]:
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def _bounded_run(name: str) -> ExecutionResult:
            async with semaphore:
                return await self._execute_agent_with_resources(
                    agent=agent_registry[name],
                    agent_name=name,
                    inputs=layer_inputs[name],
                    layer_number=layer.layer_number,
                    declared_req=dependency_graph.agents[name].resource_requirements,
                    tracker=tracker,
                )

        gathered = await asyncio.gather(
            *(_bounded_run(name) for name in layer.agents),
            return_exceptions=True,
        )

        layer_results: dict[str, ExecutionResult] = {}
        for name, outcome in zip(layer.agents, gathered, strict=True):
            if isinstance(outcome, BaseException):
                err = outcome if isinstance(outcome, Exception) else RuntimeError(str(outcome))
                layer_results[name] = ExecutionResult(
                    agent_name=name,
                    output=None,
                    success=False,
                    error=err,
                    layer_number=layer.layer_number,
                )
            else:
                layer_results[name] = outcome
        return layer_results

    async def _invoke_agent_callable(self, agent: Any, inputs: dict[str, Any]) -> Any:
        if hasattr(agent, "execute") and callable(agent.execute):
            raw = agent.execute(inputs)
        elif callable(agent):
            raw = agent(inputs)
        else:
            raise TypeError(f"Agent object {agent!r} is neither callable nor has execute()")
        return await raw if inspect.isawaitable(raw) else raw

    async def _execute_agent_with_resources(
        self,
        *,
        agent: Any,
        agent_name: str,
        inputs: dict[str, Any],
        layer_number: int,
        declared_req: ResourceRequirements,
        tracker: dict[str, int],
    ) -> ExecutionResult:
        # Prefer explicit requirements on agent object if present, else DAG AgentDefinition
        req = getattr(agent, "resource_requirements", None)
        effective_req = req if isinstance(req, ResourceRequirements) else declared_req

        acquired_req: ResourceRequirements | None = None
        started = time.monotonic()
        in_tracker = False
        try:
            acquired_req = await self.resource_manager.acquire(effective_req)
            started = time.monotonic()
            tracker["current"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["current"])
            in_tracker = True

            logger.info("Executing agent %s (layer=%d)", agent_name, layer_number)
            output = await self._invoke_agent_callable(agent, inputs)
            completed = time.monotonic()
            return ExecutionResult(
                agent_name=agent_name,
                output=output,
                success=True,
                duration_seconds=completed - started,
                layer_number=layer_number,
                started_at=started,
                completed_at=completed,
            )
        except Exception as exc:
            completed = time.monotonic()
            logger.error("Agent %s failed after %.3fs: %s", agent_name, completed - started, exc)
            return ExecutionResult(
                agent_name=agent_name,
                output=None,
                success=False,
                error=exc,
                duration_seconds=completed - started,
                layer_number=layer_number,
                started_at=started,
                completed_at=completed,
            )
        finally:
            if in_tracker:
                tracker["current"] = max(0, tracker["current"] - 1)
            if acquired_req is not None:
                await self.resource_manager.release(acquired_req)
