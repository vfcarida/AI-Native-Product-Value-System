"""
Agent dependency graph analysis and topological execution layer planning (EVO-028).

Uses NetworkX directed acyclic graphs (DAGs) to validate agent dependencies,
compute parallel execution layers, verify pairwise parallelism constraints,
estimate critical-path speedups, and export Mermaid visualizations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import networkx as nx


@dataclass
class ResourceRequirements:
    """Declared compute and API resource requirements for an agent execution."""

    cpu_cores: float = 1.0
    memory_mb: int = 256
    llm_calls: int = 1

    def __post_init__(self) -> None:
        if self.cpu_cores <= 0:
            raise ValueError("cpu_cores must be positive")
        if self.memory_mb < 0:
            raise ValueError("memory_mb must be non-negative")
        if self.llm_calls < 0:
            raise ValueError("llm_calls must be non-negative")


@dataclass
class AgentDefinition:
    """Declarative metadata describing an agent's dependencies and resource profile."""

    name: str
    depends_on: list[str] = field(default_factory=list)
    can_run_parallel_with: list[str] = field(default_factory=list)
    cannot_run_parallel_with: list[str] = field(default_factory=list)
    output_type: str = ""
    priority: int = 0
    resource_requirements: ResourceRequirements = field(default_factory=ResourceRequirements)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("AgentDefinition.name must be a non-empty string")
        if not self.output_type:
            self.output_type = self.name


@dataclass
class ExecutionLayer:
    """A batch of independent agents that can execute concurrently."""

    layer_number: int
    agents: list[str]
    dependencies: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer_number": self.layer_number,
            "agents": list(self.agents),
            "dependencies": list(self.dependencies),
        }


class DependencyGraph:
    """
    DAG-based dependency graph for parallel agent execution planning.

    Models ``upstream -> downstream`` edges so topological generations group
    mutually independent agents into concurrent execution layers.
    """

    def __init__(self) -> None:
        self.graph: nx.DiGraph[str] = nx.DiGraph()
        self.agents: dict[str, AgentDefinition] = {}

    def add_agent(self, agent: AgentDefinition) -> None:
        """Register an agent and add directed edges from each dependency."""
        self.agents[agent.name] = agent
        self.graph.add_node(agent.name)
        for dep in agent.depends_on:
            self.graph.add_edge(dep, agent.name)

    def add_dependency(self, upstream: str, downstream: str) -> None:
        """Add an explicit directed dependency edge ``upstream -> downstream``."""
        if upstream not in self.agents:
            raise KeyError(f"Unknown upstream agent: {upstream!r}")
        if downstream not in self.agents:
            raise KeyError(f"Unknown downstream agent: {downstream!r}")
        if upstream not in self.agents[downstream].depends_on:
            self.agents[downstream].depends_on.append(upstream)
        self.graph.add_edge(upstream, downstream)

    def validate(self) -> None:
        """Verify all dependency nodes exist and the graph contains no cycles."""
        missing = sorted(node for node in self.graph.nodes if node not in self.agents)
        if missing:
            raise ValueError(f"Dependency graph references unregistered agents: {missing}")

        if not nx.is_directed_acyclic_graph(self.graph):
            cycles = [list(c) for c in nx.simple_cycles(self.graph)]
            raise ValueError(f"Dependency graph has cycles: {cycles}")

    def get_direct_dependencies(self, agent_name: str) -> list[str]:
        """Return deterministically sorted direct upstream dependencies for ``agent_name``."""
        if agent_name not in self.agents:
            raise KeyError(f"Unknown agent: {agent_name!r}")
        return sorted(self.graph.predecessors(agent_name))

    def can_run_parallel(self, agent1: str, agent2: str) -> bool:
        """
        Return True if ``agent1`` and ``agent2`` may safely run concurrently.

        Two agents can run in parallel when:
        1. They are distinct registered agents.
        2. Neither has a directed dependency path to the other.
        3. Neither excludes the other via ``cannot_run_parallel_with`` or an
           explicit ``can_run_parallel_with`` allowlist.
        """
        if agent1 not in self.agents:
            raise KeyError(f"Unknown agent: {agent1!r}")
        if agent2 not in self.agents:
            raise KeyError(f"Unknown agent: {agent2!r}")
        if agent1 == agent2:
            return False

        if nx.has_path(self.graph, agent1, agent2) or nx.has_path(self.graph, agent2, agent1):
            return False

        a1 = self.agents[agent1]
        a2 = self.agents[agent2]

        if agent2 in a1.cannot_run_parallel_with or agent1 in a2.cannot_run_parallel_with:
            return False
        if a1.can_run_parallel_with and agent2 not in a1.can_run_parallel_with:
            return False
        if a2.can_run_parallel_with and agent1 not in a2.can_run_parallel_with:
            return False

        return True

    def _split_incompatible_group(self, group: list[str]) -> list[list[str]]:
        """Partition a topological generation so every pair in a sub-layer can run in parallel."""
        sublayers: list[list[str]] = []
        for agent_name in sorted(group):
            placed = False
            for sub in sublayers:
                if all(self.can_run_parallel(agent_name, peer) for peer in sub):
                    sub.append(agent_name)
                    placed = True
                    break
            if not placed:
                sublayers.append([agent_name])
        return sublayers

    def get_execution_layers(self) -> list[ExecutionLayer]:
        """
        Compute ordered execution layers via topological generations.

        Layer 0 contains agents with no prerequisites. Each subsequent layer
        contains agents whose dependencies all completed in earlier layers and
        that satisfy ``can_run_parallel()`` pairwise.
        """
        self.validate()
        if self.graph.number_of_nodes() == 0:
            return []

        layers: list[ExecutionLayer] = []
        layer_idx = 0
        for raw_generation in nx.topological_generations(self.graph):
            for compatible_group in self._split_incompatible_group(list(raw_generation)):
                deps_set: set[str] = set()
                for agent_name in compatible_group:
                    deps_set.update(self.graph.predecessors(agent_name))
                layers.append(
                    ExecutionLayer(
                        layer_number=layer_idx,
                        agents=compatible_group,
                        dependencies=sorted(deps_set),
                    )
                )
                layer_idx += 1

        return layers

    def get_critical_path(self, durations: dict[str, float] | None = None) -> list[str]:
        """Return the longest dependency path (by step count or weighted duration)."""
        self.validate()
        if self.graph.number_of_nodes() == 0:
            return []
        if not durations:
            return list(nx.dag_longest_path(self.graph))

        weighted: nx.DiGraph[str] = nx.DiGraph()
        for node in self.graph.nodes:
            weighted.add_node(node)
        for u, v in self.graph.edges:
            weighted.add_edge(u, v, weight=float(durations.get(u, 1.0)))

        # Compute exact longest node-weighted path via topological DP
        best_cost: dict[str, float] = {}
        best_prev: dict[str, str | None] = {}
        for node in nx.topological_sort(self.graph):
            node_dur = float(durations.get(node, 1.0))
            preds = list(self.graph.predecessors(node))
            if not preds:
                best_cost[node] = node_dur
                best_prev[node] = None
            else:
                best_p = max(preds, key=lambda p: best_cost[p])
                best_cost[node] = best_cost[best_p] + node_dur
                best_prev[node] = best_p

        end_node = max(best_cost, key=lambda n: best_cost[n])
        path: list[str] = []
        curr: str | None = end_node
        while curr is not None:
            path.append(curr)
            curr = best_prev[curr]
        path.reverse()
        return path

    def estimate_speedup(self, durations: dict[str, float]) -> dict[str, float]:
        """Estimate sequential vs. layered-parallel latency and speedup factor."""
        layers = self.get_execution_layers()
        if not layers:
            return {
                "sequential_seconds": 0.0,
                "parallel_seconds": 0.0,
                "speedup": 1.0,
                "latency_reduction_pct": 0.0,
            }

        seq_total = sum(float(durations.get(name, 0.0)) for name in self.agents)
        par_total = sum(
            max((float(durations.get(name, 0.0)) for name in layer.agents), default=0.0)
            for layer in layers
        )
        speedup = (seq_total / par_total) if par_total > 0 else 1.0
        reduction_pct = ((seq_total - par_total) / seq_total * 100.0) if seq_total > 0 else 0.0
        return {
            "sequential_seconds": round(seq_total, 4),
            "parallel_seconds": round(par_total, 4),
            "speedup": round(speedup, 4),
            "latency_reduction_pct": round(reduction_pct, 2),
        }

    def visualize(self) -> str:
        """Render a Mermaid ``graph TD`` diagram of the dependency graph."""
        lines = ["graph TD"]
        if self.graph.number_of_nodes() == 0:
            return "\n".join(lines)

        isolated = sorted(
            node
            for node in self.graph.nodes
            if self.graph.in_degree(node) == 0 and self.graph.out_degree(node) == 0
        )
        for node in isolated:
            lines.append(f"    {node}")

        for upstream, downstream in sorted(self.graph.edges):
            lines.append(f"    {upstream} --> {downstream}")

        return "\n".join(lines)


def build_dependency_graph(agents: list[AgentDefinition]) -> DependencyGraph:
    """Build and validate a ``DependencyGraph`` from a list of ``AgentDefinition`` objects."""
    dag = DependencyGraph()
    for agent in agents:
        dag.add_agent(agent)
    dag.validate()
    return dag
