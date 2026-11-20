"""
Spec-Kit Patterns: structured templates, completeness validator, and traceability (EVO-020).
"""

from product_value_system.spec_kit.manager import DiscoverySpecGenerator, SpecManager
from product_value_system.spec_kit.templates import Template, TemplateLibrary, TemplateType
from product_value_system.spec_kit.traceability import TraceabilityLinker, TraceLink
from product_value_system.spec_kit.validator import (
    SpecValidator,
    ValidationIssue,
    ValidationResult,
)

__all__ = [
    "DiscoverySpecGenerator",
    "SpecManager",
    "SpecValidator",
    "Template",
    "TemplateLibrary",
    "TemplateType",
    "TraceLink",
    "TraceabilityLinker",
    "ValidationIssue",
    "ValidationResult",
]
