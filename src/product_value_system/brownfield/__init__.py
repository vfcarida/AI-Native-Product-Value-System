"""
Brownfield codebase temporal analysis & Strangler Fig modernization package (EVO-030, EVO-031):
git history extraction, hotspot detection, temporal coupling, code age, ownership,
module boundary analysis, Anti-Corruption Layer generation, traffic routing, and planning.
"""

from product_value_system.brownfield.acl_generator import ACLGenerator, APIFunctionSpec
from product_value_system.brownfield.age_analyzer import AgeAnalyzer, FileAge
from product_value_system.brownfield.boundary_analyzer import (
    BoundaryAnalyzer,
    Module,
    normalize_priority_weights,
)
from product_value_system.brownfield.coupling_analyzer import Coupling, CouplingAnalyzer
from product_value_system.brownfield.git_analyzer import (
    GitAnalyzer,
    GitInsights,
    load_brownfield_config,
)
from product_value_system.brownfield.hotspot_detector import Hotspot, HotspotDetector
from product_value_system.brownfield.models import CommitChange, anonymize_author
from product_value_system.brownfield.ownership_analyzer import Ownership, OwnershipAnalyzer
from product_value_system.brownfield.routing_strategy import (
    RolloutStage,
    RoutingStrategyPlanner,
    RoutingStrategyType,
    TrafficRoutingPlan,
    should_route_to_modern,
)
from product_value_system.brownfield.strangler_planner import (
    ReplacementStep,
    StranglerPlan,
    StranglerPlanner,
    load_strangler_config,
)

__all__ = [
    "ACLGenerator",
    "APIFunctionSpec",
    "AgeAnalyzer",
    "BoundaryAnalyzer",
    "CommitChange",
    "Coupling",
    "CouplingAnalyzer",
    "FileAge",
    "GitAnalyzer",
    "GitInsights",
    "Hotspot",
    "HotspotDetector",
    "Module",
    "Ownership",
    "OwnershipAnalyzer",
    "ReplacementStep",
    "RolloutStage",
    "RoutingStrategyPlanner",
    "RoutingStrategyType",
    "StranglerPlan",
    "StranglerPlanner",
    "TrafficRoutingPlan",
    "anonymize_author",
    "load_brownfield_config",
    "load_strangler_config",
    "normalize_priority_weights",
    "should_route_to_modern",
]
