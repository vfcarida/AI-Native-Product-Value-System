"""
Architecture review agent: Independent architectural soundness review (EVO-013).
"""

import json
from typing import Any

from product_value_system.agents.base_review_agent import BaseReviewAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.review.models import FindingCategory


class ArchitectureReviewAgent(BaseReviewAgent):
    """
    Independent architecture reviewer operating in Goldfish (cleared memory) mode.

    Process:
    1. Load specification in cleared memory context
    2. Review functional requirements for structural completeness
    3. Evaluate architecture decisions for soundness and trade-offs
    4. Assess scalability, reliability, and component boundaries
    5. Check for anti-patterns (tight coupling, SPOFs, god services)
    6. Produce schema-valid evidence artifact with structured findings
    """

    review_type = "architecture"
    evidence_type = "architecture_review"
    default_category = FindingCategory.ARCHITECTURE

    def __init__(self, schema_validator: SchemaValidator) -> None:
        super().__init__(
            schema_validator,
            name="architecture-review-agent",
            version="0.1.0",
            capabilities=["architecture_review", "technical_validation"],
            required_tools=["schema.validate", "pattern.lookup"],
        )

    def _build_system_prompt(self) -> str:
        """Build system prompt for architecture review."""
        return (
            "You are an expert technical architect conducting independent "
            "architecture reviews in fresh-context (Goldfish) mode.\n\n"
            "Your role:\n"
            "- Review technical specifications for soundness and completeness\n"
            "- Identify architectural risks and anti-patterns\n"
            "- Evaluate scalability, reliability, resilience, and component boundaries\n"
            "- Provide constructive recommendations\n\n"
            "Review criteria:\n"
            "- Completeness: All functional requirements testable and clear\n"
            "- Architecture decisions: Justified with rationale and trade-offs\n"
            "- Quality attributes: Realistic performance/scalability/reliability targets\n"
            "- Anti-patterns: Identify tight coupling, SPOFs, missing idempotency, god objects\n\n"
            "Finding severity:\n"
            "- CRITICAL: Fundamental flaw, will cause failure\n"
            "- HIGH: Significant risk, major rework likely\n"
            "- MEDIUM: Important issue, should address\n"
            "- LOW: Minor concern, nice to improve\n\n"
            "Be CONSTRUCTIVE and SPECIFIC. Cite requirement IDs and architecture decision IDs."
        )

    def _build_review_prompt(self, spec: dict[str, Any]) -> str:
        """Build user prompt with specification details."""
        reqs_text = "\n".join(
            f"- {r['id']}: {r['description']} (priority: {r.get('priority', 'must')})"
            for r in spec["functional_requirements"]
        )

        decisions_text = "\n".join(
            f"- {d['id']}: {d['decision']}\n  Rationale: {d['rationale']}"
            for d in spec.get("architecture_decisions", [])
        )

        return f"""Conduct an independent architecture review of this specification:

## Specification ID
{spec['id']}

## Functional Requirements
{reqs_text}

## Architecture Decisions
{decisions_text}

## Quality Attributes
{json.dumps(spec.get('quality_attributes', {}), indent=2)}

## Constraints
{json.dumps(spec.get('constraints', {}), indent=2)}

## Review Task
Evaluate this specification for:
1. Completeness of requirements and acceptance criteria
2. Soundness of architecture decisions
3. Realism of quality attributes (performance, scalability, reliability)
4. Presence of anti-patterns or architectural risks
5. Maintainability and testability considerations

Respond in JSON format."""
