"""
Security review agent: Independent security assessment.
"""

import json
from datetime import UTC, datetime
from typing import Any

from product_value_system.agents.base_agent import BaseAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.tracing import traced


class SecurityReviewAgent(BaseAgent):
    """
    Security review agent identifies security risks in specifications.

    Process:
    1. Load specification (fresh context, no history)
    2. Review for authentication/authorization requirements
    3. Check data protection and encryption
    4. Identify injection/XSS/CSRF risks
    5. Evaluate input validation requirements
    6. Check secret management approach
    7. Generate security findings with severity
    8. Produce evidence artifact
    """

    def __init__(self, schema_validator: SchemaValidator) -> None:
        """
        Initialize security review agent.

        Args:
            schema_validator: Schema validator for specification/evidence
        """
        super().__init__(
            name="security-review-agent",
            version="0.1.0",
            capabilities=["security_review", "threat_identification"],
            required_tools=["schema.validate", "threat.lookup"],
            input_schema="https://pvs.ai/schemas/specification.schema.json",
            output_schema="https://pvs.ai/schemas/evidence.schema.json",
        )
        self.schema_validator = schema_validator

    @traced("security_review_agent.execute")
    async def execute(  # type: ignore[override]
        self,
        specification_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> dict[str, Any]:
        """
        Execute security review.

        Args:
            specification_artifact: Validated specification.yaml content
            harness: Harness providing model (no memory/history)
            context: Execution context (run_id, budgets, etc.)

        Returns:
            Evidence artifact with security findings
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
            review_type="security",
        )

        self.schema_validator.validate(evidence, "evidence.schema.json")

        return evidence

    def _build_system_prompt(self) -> str:
        """Build system prompt for security review."""
        return (
            "You are a security expert conducting independent security reviews "
            "of technical specifications.\n\n"
            "Your role:\n"
            "- Identify security risks and vulnerabilities in specifications\n"
            "- Evaluate authentication, authorization, and access control\n"
            "- Check data protection and encryption requirements\n"
            "- Detect potential injection, XSS, CSRF vulnerabilities\n"
            "- Review input validation and output sanitization\n"
            "- Assess secret management approach\n\n"
            "Finding severity:\n"
            "- CRITICAL: Exploitable vulnerability, immediate risk\n"
            "- HIGH: Significant security gap, likely exploitable\n"
            "- MEDIUM: Security weakness, should address\n"
            "- LOW: Minor concern, defense in depth\n\n"
            "Be SPECIFIC about threats and ACTIONABLE in recommendations."
        )

    def _build_review_prompt(self, spec: dict[str, Any]) -> str:
        """Build user prompt with specification for security review."""
        reqs_text = "\n".join(
            [f"- {r['id']}: {r['description']}" for r in spec["functional_requirements"]]
        )

        security_attrs = spec.get("quality_attributes", {}).get("security", [])
        if isinstance(security_attrs, list):
            security_text = (
                "\n".join([f"- {s}" for s in security_attrs])
                if security_attrs
                else "(none specified)"
            )
        else:
            security_text = json.dumps(security_attrs)

        return f"""Conduct an independent security review of this specification:

## Specification ID
{spec['id']}

## Functional Requirements
{reqs_text}

## Security Attributes (specified)
{security_text}

## Quality Attributes
{json.dumps(spec.get('quality_attributes', {}), indent=2)}

## Security Review Task
Identify security risks in this specification:
1. Authentication and authorization requirements (missing or weak?)
2. Data protection (encryption, PII handling)
3. Input validation and output encoding requirements
4. Injection vulnerabilities (SQL, command, XSS, CSRF)
5. Secret management approach
6. Audit logging for security events
7. Rate limiting and DoS prevention

Respond in JSON format."""

    def _parse_review_response(
        self,
        response_content: str,
        specification: dict[str, Any],
        review_type: str,
    ) -> dict[str, Any]:
        """Parse security review response into evidence artifact."""
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
