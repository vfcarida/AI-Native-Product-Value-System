"""
Specification agent: Outcome -> Detailed specification.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_value_system.agents.base_agent import BaseAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.tracing import traced


class SpecificationAgent(BaseAgent):
    """
    Specification agent converts outcomes into detailed specifications.

    Process:
    1. Load outcome hypothesis
    2. Analyze success criteria and constraints
    3. Generate functional requirements
    4. Define architecture approach
    5. Specify quality attributes
    6. Create testability criteria
    7. Document architectural decisions
    8. Validate output against schema
    """

    def __init__(
        self,
        schema_validator: SchemaValidator,
        architecture_patterns_path: Path | None = None,
    ) -> None:
        """
        Initialize specification agent.

        Args:
            schema_validator: Schema validator for outcome/specification
            architecture_patterns_path: Path to architecture pattern library (optional)
        """
        super().__init__(
            name="specification-agent",
            version="0.1.0",
            capabilities=["requirement_analysis", "specification_generation"],
            required_tools=[
                "knowledge.query",
                "spec.template.load",
                "architecture.pattern.search",
                "schema.validate",
            ],
            input_schema="https://pvs.ai/schemas/outcome.schema.json",
            output_schema="https://pvs.ai/schemas/specification.schema.json",
        )
        self.schema_validator = schema_validator
        self.architecture_patterns_path = architecture_patterns_path

    @traced("specification_agent.execute")
    async def execute(  # type: ignore[override]
        self,
        outcome_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> dict[str, Any]:
        """
        Execute specification generation process.

        Args:
            outcome_artifact: Validated outcome.yaml content
            harness: Harness providing model, tools, memory
            context: Execution context (run_id, budgets, etc.)

        Returns:
            Specification artifact (dict conforming to specification.schema.json)
        """
        self.schema_validator.validate(outcome_artifact, "outcome.schema.json")

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_specification_prompt(outcome_artifact)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await harness.invoke_model(
            messages=messages,
            temperature=0.2,
            max_tokens=4000,
        )

        specification = self._parse_specification_response(
            response.content,
            outcome_artifact,
        )

        self.schema_validator.validate(specification, "specification.schema.json")

        return specification

    def _build_system_prompt(self) -> str:
        """Build system prompt for specification agent."""
        return (
            "You are a technical specification agent specializing in "
            "requirement analysis and system design.\n\n"
            "Your role:\n"
            "- Convert outcome hypotheses into detailed technical specifications\n"
            "- Define functional requirements with clear acceptance criteria\n"
            "- Document architecture decisions and rationale\n"
            "- Specify quality attributes (performance, security, scalability)\n"
            "- Create comprehensive testability criteria\n\n"
            "Principles:\n"
            "- Every requirement MUST be testable\n"
            "- Link all requirements to outcome success criteria\n"
            "- Be COMPLETE but not EXHAUSTIVE (cover essentials, avoid over-specification)\n"
            "- Make architecture decisions EXPLICIT with rationale\n"
            "- Define ACCEPTANCE CRITERIA for every requirement\n"
            "- Consider CONSTRAINTS from outcome (cost, time, technology)\n\n"
            "Output format:\n"
            "Provide a structured JSON response with:\n"
            "- functional_requirements: List of requirements with acceptance criteria\n"
            "- architecture_decisions: Key technical decisions with rationale\n"
            "- quality_attributes: Performance, security, scalability requirements\n"
            "- testability_criteria: How to verify each requirement\n"
            "- constraints: Technical and operational constraints\n\n"
            "Follow specification best practices: clear, complete, consistent, testable."
        )

    def _build_specification_prompt(self, outcome: dict[str, Any]) -> str:
        """Build user prompt with outcome details."""
        success_criteria = outcome["success_criteria"]

        guardrails_text = "\n".join(
            [
                f"- {g['metric']}: {g['direction']} {g['threshold']}"
                for g in success_criteria.get("guardrails", [])
            ]
        )

        evidence_text = "\n".join(
            [
                f"- {e['type']}: {e['description']} (mandatory: {e.get('mandatory', False)})"
                for e in outcome.get("evidence_requirements", [])
            ]
        )

        return f"""Generate a detailed technical specification for this outcome:

## Outcome Hypothesis
{outcome['hypothesis']}

## Success Criteria
Primary Metric: {success_criteria['primary_metric']['name']}
- Unit: {success_criteria['primary_metric']['unit']}
- Measurement: {success_criteria['primary_metric']['measurement_method']}
- Baseline: {success_criteria['baseline']}
- Target: {success_criteria['target']}

Guardrails:
{guardrails_text or '(none)'}

## Evidence Requirements
{evidence_text or '(none)'}

## Task
Create a comprehensive specification with:

1. **Functional Requirements**: What the system must do
   - Each requirement MUST have clear acceptance criteria
   - Link to success criteria
   - Include priority (must-have, should-have, nice-to-have)

2. **Architecture Decisions**: Key technical choices
   - Decision statement
   - Rationale (why this choice)
   - Alternatives considered
   - Trade-offs

3. **Quality Attributes**: Non-functional requirements
   - Performance (latency, throughput)
   - Security (authentication, authorization, data protection)
   - Scalability (concurrent users, data volume)
   - Reliability (uptime, error rate)

4. **Testability Criteria**: How to verify requirements
   - Unit test requirements
   - Integration test requirements
   - Acceptance test scenarios

5. **Constraints**: Limitations and boundaries
   - Technical constraints (languages, frameworks, platforms)
   - Operational constraints (deployment, monitoring)
   - Business constraints (cost, timeline)

Respond in JSON format."""

    def _parse_specification_response(
        self,
        response_content: str,
        outcome: dict[str, Any],
    ) -> dict[str, Any]:
        """Parse model response into specification artifact."""
        content = response_content.strip()
        if content.startswith("```json"):
            content = content.split("```json")[1].split("```")[0].strip()
        elif content.startswith("```"):
            content = content.split("```")[1].split("```")[0].strip()

        parsed: dict[str, Any] = json.loads(content)

        outcome_id = str(outcome["id"])
        spec_id = outcome_id.replace("outcome-", "spec-", 1)

        now = datetime.now(UTC).isoformat()

        specification: dict[str, Any] = {
            "id": spec_id,
            "version": 1,
            "created": now,
            "updated": now,
            "outcome_id": outcome_id,
            "functional_requirements": parsed["functional_requirements"],
            "architecture_decisions": parsed.get("architecture_decisions", []),
            "quality_attributes": parsed.get("quality_attributes", {}),
            "testability_criteria": parsed.get("testability_criteria", {}),
            "constraints": parsed.get("constraints", {}),
        }

        return specification
