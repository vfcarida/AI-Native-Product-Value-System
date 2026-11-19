"""
Synthetic customer simulation module powered by Microsoft TinyTroupe (EVO-009).
"""

from product_value_system.simulation.persona_factory import PersonaFactory
from product_value_system.simulation.population_generator import (
    DemographicSegment,
    PopulationGenerator,
    PopulationSpec,
)
from product_value_system.simulation.scenario_runner import ScenarioRunner
from product_value_system.simulation.simulation_validator import (
    SimulationValidator,
    StatisticalTestResult,
    ValidationReport,
)
from product_value_system.simulation.tinytroupe_adapter import (
    TINYTROUPE_AVAILABLE,
    LocalTinyPerson,
    LocalTinyWorld,
    PersonaDefinition,
    SimulationResult,
    TinyTroupeAdapter,
)

__all__ = [
    "TINYTROUPE_AVAILABLE",
    "DemographicSegment",
    "LocalTinyPerson",
    "LocalTinyWorld",
    "PersonaDefinition",
    "PersonaFactory",
    "PopulationGenerator",
    "PopulationSpec",
    "ScenarioRunner",
    "SimulationResult",
    "SimulationValidator",
    "StatisticalTestResult",
    "TinyTroupeAdapter",
    "ValidationReport",
]
