"""
Persona Factory for generating synthetic customer populations from YAML templates (EVO-009).
"""

import random
from pathlib import Path
from typing import Any

import yaml

from product_value_system.simulation.tinytroupe_adapter import PersonaDefinition


class PersonaFactory:
    """
    Generate persona populations from demographic/behavioral templates.

    Loads persona templates (``tech_early_adopter``, ``mainstream_user``,
    ``accessibility_focused``, etc.) and samples diverse populations within
    the configured demographic and Big-Five personality ranges.
    """

    def __init__(
        self,
        template_path: Path | str,
        *,
        seed: int | None = None,
    ) -> None:
        """
        Initialize factory with persona templates.

        Args:
            template_path: Path to ``persona_templates.yaml``
            seed: Optional RNG seed for reproducible synthetic populations
        """
        self.template_path = Path(template_path)
        if not self.template_path.exists():
            raise FileNotFoundError(f"Persona templates not found: {self.template_path}")

        loaded = yaml.safe_load(self.template_path.read_text(encoding="utf-8")) or {}
        self.templates: dict[str, dict[str, Any]] = dict(loaded.get("templates", {}))
        self._rng = random.Random(seed)

    @property
    def available_templates(self) -> list[str]:
        """Return list of loaded template names."""
        return sorted(self.templates.keys())

    def generate_population(
        self,
        template: str,
        size: int,
        diversity_factor: float = 0.3,
    ) -> list[PersonaDefinition]:
        """
        Generate a synthetic population from a single template.

        Args:
            template: Template name (e.g., ``tech_early_adopter``)
            size: Number of personas to generate (must be >= 0)
            diversity_factor: Trait variance scale in [0.0, 1.0]

        Returns:
            List of ``PersonaDefinition`` instances
        """
        if template not in self.templates:
            raise ValueError(
                f"Template '{template}' not found. Available: {self.available_templates}"
            )
        if size < 0:
            raise ValueError("Population size must be non-negative")

        template_spec = self.templates[template]
        return [
            self._generate_persona(
                name=f"{template}_{idx + 1}",
                template_spec=template_spec,
                diversity_factor=diversity_factor,
            )
            for idx in range(size)
        ]

    def generate_mixed_population(
        self,
        template_distribution: dict[str, float],
        total_size: int,
        diversity_factor: float = 0.3,
    ) -> list[PersonaDefinition]:
        """
        Generate a mixed population across multiple templates according to proportions.

        Args:
            template_distribution: Mapping of template name -> proportion (sums to ~1.0)
            total_size: Total number of personas to generate
            diversity_factor: Trait variance scale in [0.0, 1.0]

        Returns:
            Combined list of ``PersonaDefinition`` instances of length ``total_size``
        """
        if not template_distribution:
            raise ValueError("template_distribution cannot be empty")
        if total_size < 0:
            raise ValueError("total_size must be non-negative")

        weight_sum = sum(template_distribution.values())
        if weight_sum <= 0:
            raise ValueError("template_distribution weights must sum to > 0")

        population: list[PersonaDefinition] = []
        items = list(template_distribution.items())
        allocated = 0

        for idx, (template, weight) in enumerate(items):
            if idx == len(items) - 1:
                count = max(0, total_size - allocated)
            else:
                count = int(round(total_size * (weight / weight_sum)))
                count = min(count, max(0, total_size - allocated))
            allocated += count
            population.extend(
                self.generate_population(
                    template=template,
                    size=count,
                    diversity_factor=diversity_factor,
                )
            )

        return population

    def _generate_persona(
        self,
        name: str,
        template_spec: dict[str, Any],
        diversity_factor: float,
    ) -> PersonaDefinition:
        """Generate a single persona by sampling within template distributions."""
        demographics = self._sample_demographics(
            template_spec.get("demographics", {}),
            diversity_factor,
        )
        personality = self._sample_personality(
            template_spec.get("personality", {}),
            diversity_factor,
        )
        behaviors = dict(template_spec.get("behaviors", {}))
        context_awareness = dict(template_spec.get("context_awareness", {}))
        raw_access = template_spec.get("accessibility_needs")
        accessibility_needs = list(raw_access) if raw_access else None

        return PersonaDefinition(
            name=name,
            demographics=demographics,
            personality=personality,
            behaviors=behaviors,
            context_awareness=context_awareness,
            accessibility_needs=accessibility_needs,
        )

    def _sample_demographics(
        self,
        template: dict[str, Any],
        diversity_factor: float,
    ) -> dict[str, Any]:
        """Sample demographic attributes from ranges and categorical sets."""
        demographics: dict[str, Any] = {}

        if "age_range" in template:
            min_age, max_age = template["age_range"]
            demographics["age"] = self._rng.randint(int(min_age), int(max_age))

        if "education" in template and template["education"]:
            demographics["education"] = self._rng.choice(list(template["education"]))

        if "income_range" in template:
            min_income, max_income = template["income_range"]
            demographics["income"] = self._rng.randint(int(min_income), int(max_income))

        if "occupations" in template and template["occupations"]:
            demographics["occupation"] = self._rng.choice(list(template["occupations"]))

        return demographics

    def _sample_personality(
        self,
        template: dict[str, str],
        diversity_factor: float,
    ) -> dict[str, str]:
        """Sample Big-Five personality levels with bounded variance."""
        level_map = {"low": 0.3, "medium": 0.6, "high": 0.9}
        personality: dict[str, str] = {}

        for trait, level in template.items():
            base_value = level_map.get(str(level).lower(), 0.6)
            variance = self._rng.uniform(-diversity_factor * 0.2, diversity_factor * 0.2)
            sampled = max(0.1, min(1.0, base_value + variance))

            if sampled < 0.45:
                personality[trait] = "low"
            elif sampled < 0.75:
                personality[trait] = "medium"
            else:
                personality[trait] = "high"

        return personality
