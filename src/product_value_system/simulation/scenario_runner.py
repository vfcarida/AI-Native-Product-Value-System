"""
Scenario Runner for executing customer simulation workflows (EVO-009).
"""

from pathlib import Path
from typing import Any

import yaml

from product_value_system.simulation.persona_factory import PersonaFactory
from product_value_system.simulation.population_generator import (
    DemographicSegment,
    PopulationGenerator,
    PopulationSpec,
)
from product_value_system.simulation.tinytroupe_adapter import (
    SimulationResult,
    TinyTroupeAdapter,
)


class ScenarioRunner:
    """
    Run customer simulation scenarios (ad evaluation, focus groups, surveys,
    and discovery hypothesis validation).
    """

    def __init__(
        self,
        adapter: TinyTroupeAdapter,
        factory: PersonaFactory,
    ) -> None:
        self.adapter = adapter
        self.factory = factory
        self.population_generator = PopulationGenerator(factory)

    async def run_scenario(self, scenario_path: Path | str) -> SimulationResult:
        """
        Execute a simulation scenario loaded from a YAML configuration file.

        Args:
            scenario_path: Path to scenario YAML file

        Returns:
            ``SimulationResult`` containing interactions, aggregated insights,
            quality metrics, and cost breakdown
        """
        path = Path(scenario_path)
        if not path.exists():
            raise FileNotFoundError(f"Scenario configuration not found: {path}")

        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        population_spec = config.get("population", {})
        total_size = int(population_spec.get("size", 10))
        diversity = float(population_spec.get("diversity_factor", 0.3))

        if "template" in population_spec:
            personas_def = self.factory.generate_population(
                template=str(population_spec["template"]),
                size=total_size,
                diversity_factor=diversity,
            )
        elif "template_distribution" in population_spec:
            dist = {
                str(k): float(v)
                for k, v in population_spec["template_distribution"].items()
            }
            segments = [
                DemographicSegment(template=tpl, proportion=prop)
                for tpl, prop in dist.items()
            ]
            personas_def = self.population_generator.generate(
                PopulationSpec(
                    total_size=total_size,
                    segments=segments,
                    diversity_factor=diversity,
                )
            )
        else:
            raise ValueError(
                "Population spec must define either 'template' or 'template_distribution'"
            )

        personas = self.adapter.create_personas(personas_def)
        scenario_name = str(config.get("scenario_name", path.stem))
        scenario_cfg = dict(config.get("scenario", {}))

        return await self.adapter.run_scenario(
            scenario_name=scenario_name,
            personas=personas,
            scenario_config=scenario_cfg,
        )

    async def run_hypothesis_validation(
        self,
        outcome_hypothesis: dict[str, Any],
        problem_context: dict[str, Any],
        *,
        population_size: int | None = None,
    ) -> SimulationResult:
        """
        Run a multi-interaction simulation to explore and validate an outcome hypothesis.

        Args:
            outcome_hypothesis: Outcome dict produced by ``DiscoveryAgent``
            problem_context: Context dictionary from ``problem.yaml``
            population_size: Optional override for number of personas

        Returns:
            ``SimulationResult`` with synthetic customer validation evidence
        """
        scenario_config = self._build_validation_scenario(
            outcome_hypothesis=outcome_hypothesis,
            problem_context=problem_context,
        )

        inferred = self._infer_population_from_context(problem_context)
        resolved_size = (
            population_size if population_size is not None else int(inferred["size"])
        )
        personas_def = self.factory.generate_mixed_population(
            template_distribution=inferred["template_distribution"],
            total_size=resolved_size,
        )
        personas = self.adapter.create_personas(personas_def)

        return await self.adapter.run_scenario(
            scenario_name="hypothesis_validation",
            personas=personas,
            scenario_config=scenario_config,
        )

    def _build_validation_scenario(
        self,
        outcome_hypothesis: dict[str, Any],
        problem_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Build 3-round validation interaction script from an outcome hypothesis."""
        hypothesis_text = str(outcome_hypothesis.get("hypothesis", ""))
        segment = str(
            problem_context.get(
                "user_segment",
                problem_context.get("domain", "general users"),
            )
        )

        return {
            "interactions": [
                {
                    "prompt": (
                        f"As a customer in the '{segment}' segment, you encounter this "
                        f"proposed improvement: {hypothesis_text}. "
                        "How would you react? Would you adopt it and why?"
                    ),
                    "target_personas": "all",
                },
                {
                    "prompt": (
                        "What are your primary concerns, friction points, or trust questions "
                        "about this proposed change?"
                    ),
                    "target_personas": "all",
                },
                {
                    "prompt": (
                        "Compared to your current experience, what specific feature or "
                        "safeguard would make this clearly valuable to you?"
                    ),
                    "target_personas": "all",
                },
            ]
        }

    def _infer_population_from_context(
        self,
        problem_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Select a domain-appropriate persona template mix from problem context."""
        context_blob = " ".join(str(v).lower() for v in problem_context.values())

        if any(kw in context_blob for kw in ("payment", "bank", "fintech", "merchant")):
            return {
                "template_distribution": {
                    "tech_early_adopter": 0.3,
                    "mainstream_user": 0.5,
                    "accessibility_focused": 0.2,
                },
                "size": 30,
            }

        return {
            "template_distribution": {
                "tech_early_adopter": 0.5,
                "mainstream_user": 0.5,
            },
            "size": 20,
        }
