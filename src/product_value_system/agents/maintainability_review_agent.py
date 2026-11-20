"""
Maintainability review agent: Independent clarity, testability, and technical debt review (EVO-013).
"""

import json
from typing import Any

from product_value_system.agents.base_review_agent import BaseReviewAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.review.models import FindingCategory


class MaintainabilityReviewAgent(BaseReviewAgent):
    """
    Independent maintainability reviewer operating in Goldfish (cleared memory) mode.

    Checklist:
    - Requirement clarity, unambiguous phrasing, consistent naming
    - Testability and explicit acceptance/coverage criteria (unit, integration, acceptance)
    - Documentation completeness (architecture decisions, API contracts, runbooks)
    - Complexity management and clean module boundaries
    - Error handling, observability, configuration management, and dependency hygiene
    """

    review_type = "maintainability"
    evidence_type = "review"
    default_category = FindingCategory.MAINTAINABILITY

    def __init__(self, schema_validator: SchemaValidator) -> None:
        super().__init__(
            schema_validator,
            name="maintainability-review-agent",
            version="0.1.0",
            capabilities=["maintainability_review", "testability_audit", "clarity_assessment"],
            required_tools=["schema.validate", "complexity.check"],
        )

    def _build_system_prompt(self) -> str:
        """Build system prompt focused on clarity, testability, and long-term maintainability."""
        return (
            "You are an independent software maintainability reviewer operating in "
            "fresh-context (Goldfish) mode.\n\n"
            "Focus areas:\n"
            "1. Clarity: unambiguous requirements, clear interfaces, consistent naming.\n"
            "2. Testability: measurable acceptance criteria, unit/integration/acceptance "
            "coverage expectations, explicit edge cases.\n"
            "3. Documentation: justified architecture decisions, interface contracts, "
            "operational and configuration clarity.\n"
            "4. Technical debt: modular boundaries, error handling, dependency hygiene, "
            "and avoidance of undocumented workarounds.\n\n"
            "Severity calibration:\n"
            "- HIGH: Untestable core requirement or severely ambiguous interface contract\n"
            "- MEDIUM: Missing edge-case criteria, low testability, or weak modularity\n"
            "- LOW: Naming, documentation polish, or minor clarity improvements\n\n"
            "Be specific and cite affected requirement IDs (FR-###) or decision IDs (AD-###)."
        )

    def _build_review_prompt(self, spec: dict[str, Any]) -> str:
        """Build user prompt with specification details for maintainability review."""
        reqs_text = "\n".join(
            f"- {r['id']}: {r['description']} | "
            f"acceptance_criteria={r.get('acceptance_criteria', [])}"
            for r in spec["functional_requirements"]
        )
        return f"""Conduct an independent maintainability review of this specification:

## Specification ID
{spec['id']}

## Functional Requirements
{reqs_text}

## Testability Criteria
{json.dumps(spec.get('testability_criteria', {}), indent=2)}

## Architecture Decisions
{json.dumps(spec.get('architecture_decisions', []), indent=2)}

## Constraints
{json.dumps(spec.get('constraints', {}), indent=2)}

## Maintainability Review Task
Identify gaps in requirement clarity, acceptance criteria, test coverage, documentation,
error handling, modularity, and technical debt management.

Respond in JSON format with `findings`, `overall_verdict`, and `summary` (or a JSON array
of finding objects with `severity`, `category`, `title`, `description`, `recommendation`,
and `requirement_id`)."""
