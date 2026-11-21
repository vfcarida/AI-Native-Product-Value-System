"""
Complexity-aware LLM Model Routing module (EVO-026).

Provides task complexity classification, cost-sorted routing policies,
schema & LLM-judge quality validation, automatic higher-tier fallback,
and FinOps cost-savings metrics.
"""

from product_value_system.routing.complexity_classifier import (
    ComplexityClassifier,
    ComplexityLevel,
    ComplexityScore,
)
from product_value_system.routing.fallback_handler import (
    AdapterFactory,
    FallbackHandler,
    QualityCheckFailedError,
)
from product_value_system.routing.model_router import (
    ModelRouter,
    RoutingDecision,
)
from product_value_system.routing.quality_validator import (
    QualityValidationResult,
    QualityValidator,
)
from product_value_system.routing.routing_policy import (
    DEFAULT_ROUTING_CONFIG,
    ModelSpec,
    RoutingPolicy,
)

__all__ = [
    "AdapterFactory",
    "ComplexityClassifier",
    "ComplexityLevel",
    "ComplexityScore",
    "DEFAULT_ROUTING_CONFIG",
    "FallbackHandler",
    "ModelRouter",
    "ModelSpec",
    "QualityCheckFailedError",
    "QualityValidationResult",
    "QualityValidator",
    "RoutingDecision",
    "RoutingPolicy",
]
