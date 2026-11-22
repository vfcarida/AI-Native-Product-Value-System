"""
Strangler Fig modernization planner orchestrating module boundary identification,
multi-factor candidate scoring, incremental replacement steps, ACL code generation,
progressive routing strategies, and risk assessment (EVO-031).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from product_value_system.brownfield.acl_generator import ACLGenerator
from product_value_system.brownfield.boundary_analyzer import (
    BoundaryAnalyzer,
    Module,
    normalize_priority_weights,
)
from product_value_system.brownfield.git_analyzer import GitInsights
from product_value_system.brownfield.routing_strategy import (
    RoutingStrategyPlanner,
    TrafficRoutingPlan,
)

logger = logging.getLogger(__name__)


def load_strangler_config(path: Path | str) -> dict[str, Any]:
    """Load the ``brownfield.strangler_fig`` configuration block from YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    bf = raw.get("brownfield", raw)
    if isinstance(bf, dict) and "strangler_fig" in bf:
        sf = bf["strangler_fig"]
        return dict(sf) if isinstance(sf, dict) else {}
    return dict(bf) if isinstance(bf, dict) else {}


@dataclass
class ReplacementStep:
    """Single actionable step within a Strangler Fig replacement plan."""

    step_number: int
    description: str
    estimated_effort_hours: int
    risk_level: str  # LOW, MEDIUM, HIGH
    prerequisites: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_number": self.step_number,
            "description": self.description,
            "estimated_effort_hours": self.estimated_effort_hours,
            "risk_level": self.risk_level,
            "prerequisites": list(self.prerequisites),
        }


@dataclass
class StranglerPlan:
    """Complete Strangler Fig modernization plan for one target module."""

    target_module: Module
    priority_score: float
    replacement_steps: list[ReplacementStep]
    acl_template: str
    routing_strategy: str
    routing_plan: TrafficRoutingPlan
    total_estimated_effort_hours: int
    risk_assessment: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_module": self.target_module.to_dict(),
            "priority_score": round(self.priority_score, 4),
            "replacement_steps": [s.to_dict() for s in self.replacement_steps],
            "acl_template": self.acl_template,
            "routing_strategy": self.routing_strategy,
            "routing_plan": self.routing_plan.to_dict(),
            "total_estimated_effort_hours": self.total_estimated_effort_hours,
            "risk_assessment": self.risk_assessment,
        }


class StranglerPlanner:
    """Plan incremental Strangler Fig modernizations from git & static analysis."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        boundary_analyzer: BoundaryAnalyzer | None = None,
        acl_generator: ACLGenerator | None = None,
        routing_planner: RoutingStrategyPlanner | None = None,
    ) -> None:
        raw = dict(config or {})
        if "brownfield" in raw and isinstance(raw["brownfield"], dict):
            raw = dict(raw["brownfield"].get("strangler_fig", raw["brownfield"]))
        elif "strangler_fig" in raw and isinstance(raw["strangler_fig"], dict):
            raw = dict(raw["strangler_fig"])

        self.config: dict[str, Any] = raw
        self.enabled: bool = bool(raw.get("enabled", True))
        self.top_candidates: int = max(1, int(raw.get("top_candidates", 3)))
        self.priority_weights: dict[str, float] = normalize_priority_weights(
            raw.get("priority_factors")
        )
        self.business_value_overrides: dict[str, float] = {
            str(k): float(v)
            for k, v in (raw.get("business_value_overrides") or {}).items()
        }

        self.boundary_analyzer = boundary_analyzer or BoundaryAnalyzer(raw)
        self.acl_generator = acl_generator or ACLGenerator()
        self.routing_planner = routing_planner or RoutingStrategyPlanner(raw)

    @classmethod
    def from_yaml(cls, path: Path | str) -> StranglerPlanner:
        return cls(load_strangler_config(path))

    async def plan(
        self,
        git_insights: GitInsights | None,
        code_structure: dict[str, Any],
        *,
        repo_path: Path | None = None,
    ) -> list[StranglerPlan]:
        """Identify candidate modules, rank them, and produce top Strangler plans."""
        if not self.enabled:
            logger.info("Strangler Fig planning disabled; returning empty plan list.")
            return []

        if not code_structure:
            return []

        insights = git_insights or GitInsights()
        modules = self.boundary_analyzer.identify_modules(
            code_structure,
            repo_path=repo_path,
        )
        if not modules:
            return []

        scored = self._score_modules(modules, insights)
        scored.sort(
            key=lambda m: (
                -m.compute_priority(self.priority_weights),
                -m.lines_of_code,
                m.name,
            )
        )

        plans: list[StranglerPlan] = []
        for idx, module in enumerate(scored[: self.top_candidates]):
            logger.info("Generating Strangler Fig plan #%d for module '%s'", idx + 1, module.name)
            plans.append(
                self._generate_plan(
                    module=module,
                    git_insights=insights,
                    code_structure=code_structure,
                    repo_path=repo_path,
                )
            )
        return plans

    def _score_modules(
        self,
        modules: list[Module],
        git_insights: GitInsights,
    ) -> list[Module]:
        """Populate normalized hotspot, temporal+structural coupling, and business value."""
        hotspot_freq_map = {h.file_path: h.change_frequency for h in git_insights.hotspots}
        max_freq = max(hotspot_freq_map.values(), default=0.0)
        freq_scale = max(max_freq, 1.0)

        temporal_coupling_map: dict[str, float] = {}
        for c in git_insights.couplings:
            temporal_coupling_map[c.file1] = max(
                temporal_coupling_map.get(c.file1, 0.0),
                c.coupling_coefficient,
            )
            temporal_coupling_map[c.file2] = max(
                temporal_coupling_map.get(c.file2, 0.0),
                c.coupling_coefficient,
            )

        for module in modules:
            freqs = [hotspot_freq_map.get(fp, 0.0) for fp in module.file_paths]
            avg_freq = (sum(freqs) / len(freqs)) if freqs else 0.0
            module.hotspot_score = round(min(1.0, avg_freq / freq_scale), 4)

            temp_couplings = [temporal_coupling_map.get(fp, 0.0) for fp in module.file_paths]
            max_temp = max(temp_couplings, default=0.0)
            module.coupling_score = round(max(module.coupling_score, max_temp), 4)

            module.business_value_score = round(
                max(0.0, min(1.0, self.business_value_overrides.get(module.name, 0.5))),
                4,
            )

        return modules

    def _generate_plan(
        self,
        *,
        module: Module,
        git_insights: GitInsights,
        code_structure: dict[str, Any],
        repo_path: Path | None,
    ) -> StranglerPlan:
        size_factor = max(1.0, min(3.0, module.lines_of_code / 1000.0))
        impl_hours = int(round(32 * size_factor))
        acl_hours = int(round(12 * (1.0 + module.coupling_score)))

        routing_plan = self.routing_planner.build_plan(module.name)
        stages_summary = " → ".join(f"{s.percentage}%" for s in routing_plan.stages)
        first_pct = routing_plan.stages[0].percentage if routing_plan.stages else 1
        mon_hours = routing_plan.stages[0].monitoring_duration_hours if routing_plan.stages else 24

        steps = [
            ReplacementStep(
                step_number=1,
                description=f"Analyze {module.name} API surface and data contracts",
                estimated_effort_hours=4,
                risk_level="LOW",
                prerequisites=[],
            ),
            ReplacementStep(
                step_number=2,
                description=f"Design modern replacement architecture for {module.name}",
                estimated_effort_hours=16,
                risk_level="MEDIUM",
                prerequisites=[1],
            ),
            ReplacementStep(
                step_number=3,
                description=f"Implement modern {module.name} with parity tests",
                estimated_effort_hours=impl_hours,
                risk_level="MEDIUM",
                prerequisites=[2],
            ),
            ReplacementStep(
                step_number=4,
                description=f"Create Anti-Corruption Layer ({module.name}ACL)",
                estimated_effort_hours=acl_hours,
                risk_level="HIGH",
                prerequisites=[2, 3],
            ),
            ReplacementStep(
                step_number=5,
                description=f"Deploy with {first_pct}% canary traffic routing",
                estimated_effort_hours=8,
                risk_level="HIGH",
                prerequisites=[4],
            ),
            ReplacementStep(
                step_number=6,
                description=f"Monitor error rate, latency & parity ({mon_hours}h per stage)",
                estimated_effort_hours=8,
                risk_level="MEDIUM",
                prerequisites=[5],
            ),
            ReplacementStep(
                step_number=7,
                description=f"Progressive rollout ({stages_summary})",
                estimated_effort_hours=16,
                risk_level="HIGH",
                prerequisites=[6],
            ),
            ReplacementStep(
                step_number=8,
                description=f"Decommission legacy {module.name} and remove ACL scaffolding",
                estimated_effort_hours=8,
                risk_level="MEDIUM",
                prerequisites=[7],
            ),
        ]

        acl_template = self.acl_generator.generate(
            module,
            code_structure,
            repo_path=repo_path,
        )
        risk_assessment = self._assess_risk(module, git_insights)
        priority_score = module.compute_priority(self.priority_weights)

        return StranglerPlan(
            target_module=module,
            priority_score=priority_score,
            replacement_steps=steps,
            acl_template=acl_template,
            routing_strategy=routing_plan.to_markdown(),
            routing_plan=routing_plan,
            total_estimated_effort_hours=sum(s.estimated_effort_hours for s in steps),
            risk_assessment=risk_assessment,
        )

    def _assess_risk(self, module: Module, git_insights: GitInsights) -> str:
        risks: list[str] = []
        if module.coupling_score > 0.7:
            risks.append("HIGH coupling: Module tightly coupled to neighboring modules")
        if module.complexity_score > 0.8:
            risks.append("HIGH complexity: Dense branching increases reimplementation risk")
        if module.lines_of_code > 5000:
            risks.append("MEDIUM size: Large module (>5K LOC) should be sliced incrementally")

        silo_files = [
            fp
            for fp in module.file_paths
            if fp in git_insights.ownership and git_insights.ownership[fp].is_knowledge_silo
        ]
        if silo_files:
            risks.append(
                f"MEDIUM ownership risk: {len(silo_files)} file(s) "
                "are single-author knowledge silos"
            )

        if not risks:
            return "LOW risk: Well-bounded module suited for Strangler Fig replacement"
        return "Risk factors:\n" + "\n".join(f"- {r}" for r in risks)
