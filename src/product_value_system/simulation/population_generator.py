"""
Stratified demographic population generator for customer simulations (EVO-009).
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from product_value_system.simulation.persona_factory import PersonaFactory
from product_value_system.simulation.tinytroupe_adapter import PersonaDefinition


@dataclass
class DemographicSegment:
    """Weighted population segment targeting a specific persona template."""

    template: str
    proportion: float
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass
class PopulationSpec:
    """Specification for generating a balanced synthetic customer population."""

    total_size: int
    segments: list[DemographicSegment]
    diversity_factor: float = 0.3
    min_segment_size: int = 1


class PopulationGenerator:
    """
    Create demographically balanced synthetic populations from ``PopulationSpec``
    and compute demographic coverage statistics.
    """

    def __init__(self, factory: PersonaFactory) -> None:
        self.factory = factory

    def generate(self, spec: PopulationSpec) -> list[PersonaDefinition]:
        """
        Generate stratified persona population honoring segment proportions and overrides.

        Args:
            spec: ``PopulationSpec`` defining total size and segment distribution

        Returns:
            List of ``PersonaDefinition`` instances
        """
        if spec.total_size <= 0:
            raise ValueError("total_size must be positive")
        if not spec.segments:
            raise ValueError("At least one DemographicSegment is required")

        weight_sum = sum(seg.proportion for seg in spec.segments)
        if weight_sum <= 0:
            raise ValueError("Segment proportions must sum to > 0")

        population: list[PersonaDefinition] = []
        allocated = 0

        for idx, segment in enumerate(spec.segments):
            if idx == len(spec.segments) - 1:
                count = max(0, spec.total_size - allocated)
            else:
                raw_count = int(round(spec.total_size * (segment.proportion / weight_sum)))
                count = (
                    max(spec.min_segment_size, raw_count)
                    if spec.total_size >= len(spec.segments)
                    else raw_count
                )
                count = min(count, max(0, spec.total_size - allocated))
            allocated += count

            segment_personas = self.factory.generate_population(
                template=segment.template,
                size=count,
                diversity_factor=spec.diversity_factor,
            )

            if segment.overrides:
                for persona in segment_personas:
                    for key, value in segment.overrides.items():
                        persona.demographics[key] = value

            population.extend(segment_personas)

        return population

    def summarize_population(self, population: list[PersonaDefinition]) -> dict[str, Any]:
        """Compute demographic & behavioral distribution summary for auditability."""
        if not population:
            return {
                "size": 0,
                "age_min": None,
                "age_max": None,
                "age_mean": None,
                "education_distribution": {},
                "adoption_distribution": {},
                "accessibility_representation": 0.0,
            }

        ages = [
            int(p.demographics["age"])
            for p in population
            if "age" in p.demographics and isinstance(p.demographics["age"], (int, float))
        ]
        educations = Counter(
            str(p.demographics.get("education", "Unknown")) for p in population
        )
        adoptions = Counter(
            str(p.behaviors.get("technology_adoption", "unknown")) for p in population
        )
        accessibility_count = sum(1 for p in population if p.accessibility_needs)

        return {
            "size": len(population),
            "age_min": min(ages) if ages else None,
            "age_max": max(ages) if ages else None,
            "age_mean": round(sum(ages) / len(ages), 1) if ages else None,
            "education_distribution": dict(educations),
            "adoption_distribution": dict(adoptions),
            "accessibility_representation": round(accessibility_count / len(population), 3),
        }
