"""
Conflict resolution for parallel agents producing overlapping output types (EVO-028).

Supports three deterministic strategies:
- ``merge``: Deep-merges dictionaries and deduplicates lists; resolves conflicting
  review verdicts conservatively (``reject`` > ``request_changes`` > ``approve``)
  and breaks generic scalar ties by agent priority.
- ``priority``: Selects the full output of the highest-priority agent.
- ``manual``: Preserves all conflicting candidates and flags ``requires_manual_review``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConflictStrategy(str, Enum):
    """Supported resolution strategies for conflicting parallel agent outputs."""

    MERGE = "merge"
    PRIORITY = "priority"
    MANUAL = "manual"


_VERDICT_SEVERITY: dict[str, int] = {
    "approve": 1,
    "approved": 1,
    "pass": 1,
    "warn": 2,
    "request_changes": 2,
    "conditional": 2,
    "reject": 3,
    "rejected": 3,
    "fail": 3,
    "block": 3,
}


@dataclass
class ConflictRecord:
    """Audit record describing a detected output conflict and how it was resolved."""

    output_type: str
    key: str
    agents: list[str]
    values: dict[str, Any]
    strategy: str
    resolved_value: Any
    requires_manual_review: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_type": self.output_type,
            "key": self.key,
            "agents": list(self.agents),
            "values": dict(self.values),
            "strategy": self.strategy,
            "resolved_value": self.resolved_value,
            "requires_manual_review": self.requires_manual_review,
        }


@dataclass
class ResolutionReport:
    """Aggregated resolved outputs and any conflicts encountered."""

    resolved_outputs: dict[str, Any] = field(default_factory=dict)
    conflicts: list[ConflictRecord] = field(default_factory=list)
    requires_manual_review: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved_outputs": dict(self.resolved_outputs),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "requires_manual_review": self.requires_manual_review,
        }


class ConflictResolver:
    """Resolve conflicting outputs emitted by parallel agents."""

    def __init__(
        self,
        strategy: str | ConflictStrategy = ConflictStrategy.MERGE,
        *,
        agent_priorities: dict[str, int] | None = None,
    ) -> None:
        self.strategy = (
            strategy
            if isinstance(strategy, ConflictStrategy)
            else ConflictStrategy(str(strategy).strip().lower())
        )
        self.agent_priorities: dict[str, int] = dict(agent_priorities or {})

    def _rank_agents(
        self,
        agent_names: list[str],
        priorities: dict[str, int],
    ) -> list[str]:
        """Sort agents from highest priority to lowest (deterministic tie-break by name)."""
        return sorted(
            agent_names,
            key=lambda name: (-priorities.get(name, self.agent_priorities.get(name, 0)), name),
        )

    def resolve(
        self,
        outputs: dict[str, Any],
        *,
        output_types: dict[str, str] | None = None,
        priorities: dict[str, int] | None = None,
    ) -> ResolutionReport:
        """
        Resolve outputs across agents grouped by ``output_type``.

        Args:
            outputs: Mapping of ``agent_name -> agent_output``.
            output_types: Optional mapping of ``agent_name -> output_type``.
                Agents without an entry default to their own ``agent_name`` (no conflict).
            priorities: Optional per-call priority overrides (higher wins).
        """
        type_map = output_types or {}
        prio_map = priorities or {}

        grouped: dict[str, dict[str, Any]] = {}
        for agent_name in sorted(outputs):
            otype = type_map.get(agent_name, agent_name)
            grouped.setdefault(otype, {})[agent_name] = outputs[agent_name]

        resolved_outputs: dict[str, Any] = {}
        conflicts: list[ConflictRecord] = []
        needs_manual = False

        for otype, agent_outputs in grouped.items():
            if len(agent_outputs) == 1:
                resolved_outputs[otype] = next(iter(agent_outputs.values()))
                continue

            ranked = self._rank_agents(list(agent_outputs.keys()), prio_map)

            if self.strategy == ConflictStrategy.PRIORITY:
                winner = ranked[0]
                resolved_val = agent_outputs[winner]
                resolved_outputs[otype] = resolved_val
                if not self._all_equal(list(agent_outputs.values())):
                    conflicts.append(
                        ConflictRecord(
                            output_type=otype,
                            key="$root",
                            agents=ranked,
                            values=dict(agent_outputs),
                            strategy=self.strategy.value,
                            resolved_value=resolved_val,
                        )
                    )

            elif self.strategy == ConflictStrategy.MANUAL:
                if self._all_equal(list(agent_outputs.values())):
                    resolved_outputs[otype] = agent_outputs[ranked[0]]
                else:
                    needs_manual = True
                    manual_payload = {
                        "requires_manual_review": True,
                        "output_type": otype,
                        "candidates": {name: agent_outputs[name] for name in ranked},
                    }
                    resolved_outputs[otype] = manual_payload
                    conflicts.append(
                        ConflictRecord(
                            output_type=otype,
                            key="$root",
                            agents=ranked,
                            values=dict(agent_outputs),
                            strategy=self.strategy.value,
                            resolved_value=manual_payload,
                            requires_manual_review=True,
                        )
                    )

            else:  # MERGE
                merged, merge_conflicts = self._merge_group(otype, ranked, agent_outputs)
                resolved_outputs[otype] = merged
                conflicts.extend(merge_conflicts)

        return ResolutionReport(
            resolved_outputs=resolved_outputs,
            conflicts=conflicts,
            requires_manual_review=needs_manual,
        )

    def _all_equal(self, values: list[Any]) -> bool:
        if not values:
            return True
        first = values[0]
        return all(v == first for v in values[1:])

    def _merge_group(
        self,
        otype: str,
        ranked_agents: list[str],
        agent_outputs: dict[str, Any],
    ) -> tuple[Any, list[ConflictRecord]]:
        values = [agent_outputs[a] for a in ranked_agents]
        if not all(isinstance(v, dict) for v in values):
            winner = agent_outputs[ranked_agents[0]]
            recs: list[ConflictRecord] = []
            if not self._all_equal(values):
                recs.append(
                    ConflictRecord(
                        output_type=otype,
                        key="$root",
                        agents=ranked_agents,
                        values=dict(agent_outputs),
                        strategy=ConflictStrategy.MERGE.value,
                        resolved_value=winner,
                    )
                )
            return winner, recs

        all_keys: list[str] = sorted({k for v in values for k in v.keys()})
        merged: dict[str, Any] = {}
        recs = []

        for key in all_keys:
            present = {
                agent: agent_outputs[agent][key]
                for agent in ranked_agents
                if key in agent_outputs[agent]
            }
            present_vals = list(present.values())

            if len(present_vals) == 1:
                merged[key] = present_vals[0]
            elif all(isinstance(v, list) for v in present_vals):
                combined: list[Any] = []
                for lst in present_vals:
                    for item in lst:
                        if item not in combined:
                            combined.append(item)
                merged[key] = combined
            elif all(isinstance(v, dict) for v in present_vals):
                sub_merged, sub_conflicts = self._merge_group(
                    otype,
                    list(present.keys()),
                    present,
                )
                merged[key] = sub_merged
                for c in sub_conflicts:
                    c.key = f"{key}.{c.key}" if c.key != "$root" else key
                    recs.append(c)
            elif self._all_equal(present_vals):
                merged[key] = present_vals[0]
            else:
                # Resolve scalar conflict: conservative severity for verdict/status, else priority
                if key in {"verdict", "gate_decision", "decision"} and all(
                    isinstance(v, str) and v.lower() in _VERDICT_SEVERITY for v in present_vals
                ):
                    resolved_scalar = max(
                        present_vals,
                        key=lambda v: _VERDICT_SEVERITY[str(v).lower()],
                    )
                else:
                    top_agent = list(present.keys())[0]
                    resolved_scalar = present[top_agent]

                merged[key] = resolved_scalar
                recs.append(
                    ConflictRecord(
                        output_type=otype,
                        key=key,
                        agents=list(present.keys()),
                        values=present,
                        strategy=ConflictStrategy.MERGE.value,
                        resolved_value=resolved_scalar,
                    )
                )

        return merged, recs
