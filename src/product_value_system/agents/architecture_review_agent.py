"""
Architecture review agent: Independent technical review.
"""

import json
from datetime import UTC, datetime
from typing import Any

from product_value_system.agents.base_agent import BaseAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.tracing import traced


class ArchitectureReviewAgent(BaseAgent):
    """
    Architecture review agent evaluates technical specifications.

    Process:
    1. Load specification (fresh context, no history)
    2. Review functional requirements for completeness
    3. Evaluate architecture decisions for soundness
    4. Assess quality attributes (performance, scalability)
    5. Check for common anti-patterns
    6. Generate findings with severity and recommendations
    7. Produce evidence artifact
    """

    def __init__(self, schema_validator: SchemaValidator) -> None:
        """
        Initialize architecture review agent.

        Args:
            schema_validator: Schema validator for specification/evidence
        """
        super().__init__(
            name="architecture-review-agent",
            version="0.1.0",
            capabilities=["architecture_review", "technical_validation"],
            required_tools=["schema.validate", "pattern.lookup"],
            input_schema="https://pvs.ai/schemas/specification.schema.json",
            output_schema="https://pvs.ai/schemas/evidence.schema.json",
        )
        self.schema_validator = schema_validator

    @traced("architecture_review_agent.execute")
    async def execute(  # type: ignore[override]
        self,
        specification_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> dict[str, Any]:
        """
        Execute architecture review.

        Args:
            specification_artifact: Validated specification.yaml content
            harness: Harness providing model (no memory/history)
            context: Execution context (run_id, budgets, etc.)

        Returns:
            Evidence artifact with architecture findings
        """
        self.schema_validator.validate(specification_artifact, "specification.schema.json")

        # Elephant-Goldfish pattern: clear any prior conversation memory
        harness.memory.clear_messages()

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_review_prompt(specification_artifact)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await harness.invoke_model(
            messages=messages,
            temperature=0.3,
            max_tokens=3000,
        )

        evidence = self._parse_review_response(
            response.content,
            specification_artifact,
            review_type="architecture",
        )

        self.schema_validator.validate(evidence, "evidence.schema.json")

        return evidence

    def _build_system_prompt(self) -> str:
        """Build system prompt for architecture review."""
        return (
            "You are an expert technical architect conducting independent "
            "architecture reviews.\n\n"
            "Your role:\n"
            "- Review technical specifications for soundness and completeness\n"
            "- Identify architectural risks and anti-patterns\n"
            "- Evaluate scalability, performance, and maintainability\n"
            "- Provide constructive recommendations\n\n"
            "Review criteria:\n"
            "- Completeness: All functional requirements testable and clear\n"
            "- Architecture decisions: Justified with rationale and trade-offs\n"
            "- Quality attributes: Realistic performance/scalability targets\n"
            "- Scalability: Design supports expected growth\n"
            "- Maintainability: Clear component boundaries, testability\n"
            "- Anti-patterns: Identify common mistakes (tight coupling, god objects, etc.)\n\n"
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
            [
                f"- {r['id']}: {r['description']} (priority: {r.get('priority', 'must')})"
                for r in spec["functional_requirements"]
            ]
        )

        decisions_text = "\n".join(
            [
                f"- {d['id']}: {d['decision']}\n  Rationale: {d['rationale']}"
                for d in spec.get("architecture_decisions", [])
            ]
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
3. Realism of quality attributes (performance, scalability)
4. Presence of anti-patterns or architectural risks
5. Maintainability and testability considerations

Respond in JSON format."""

    def _parse_review_response(
        self,
        response_content: str,
        specification: dict[str, Any],
        review_type: str,
    ) -> dict[str, Any]:
        """Parse review response into evidence artifact."""
        content = response_content.strip()
        if content.startswith("```json"):
            content = content.split("```json")[1].split("```")[0].strip()
        elif content.startswith("```"):
            content = content.split("```")[1].split("```")[0].strip()

        parsed: dict[str, Any] = json.loads(content)

        spec_id = str(specification["id"])
        evidence_id = f"{spec_id}-{review_type}-review"

        now = datetime.now(UTC).isoformat()

        evidence: dict[str, Any] = {
            "id": evidence_id,
            "version": 1,
            "created": now,
            "type": f"{review_type}_review",
            "artifact_id": spec_id,
            "verdict": parsed["overall_verdict"],
            "summary": parsed["summary"],
            "findings": parsed["findings"],
            "reviewer": f"{self.name} v{self.version}",
        }

        return evidence
