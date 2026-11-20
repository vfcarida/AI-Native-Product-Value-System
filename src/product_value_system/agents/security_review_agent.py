"""
Security review agent: Independent OWASP LLM Top 10 & AppSec assessment (EVO-013).
"""

import json
from typing import Any

from product_value_system.agents.base_review_agent import BaseReviewAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.review.models import FindingCategory


class SecurityReviewAgent(BaseReviewAgent):
    """
    Independent security reviewer identifying security risks in specifications
    using the Elephant-Goldfish pattern (cleared memory before every review).

    Checklist:
    - OWASP LLM Top 10 (LLM01 Prompt Injection, LLM02 Insecure Output Handling,
      LLM04 Model DoS, LLM06 Sensitive Info Disclosure, LLM07 Insecure Plugin Design,
      LLM08 Excessive Agency)
    - Authentication, authorization, and least-privilege access control
    - Data protection, TLS 1.3 / at-rest encryption, PII redaction
    - Injection/XSS/CSRF/SSRF defenses and secret management
    - Security audit logging and rate limiting
    """

    review_type = "security"
    evidence_type = "security_review"
    default_category = FindingCategory.SECURITY

    def __init__(self, schema_validator: SchemaValidator) -> None:
        super().__init__(
            schema_validator,
            name="security-review-agent",
            version="0.1.0",
            capabilities=["security_review", "threat_identification", "owasp_llm_audit"],
            required_tools=["schema.validate", "threat.lookup"],
        )

    def _build_system_prompt(self) -> str:
        """Build system prompt for independent OWASP LLM Top 10 security review."""
        return (
            "You are a security expert conducting independent security reviews of "
            "technical specifications in fresh-context (Goldfish) mode.\n\n"
            "Focus on OWASP LLM Top 10:\n"
            "- LLM01: Prompt Injection (input validation, length limits, pattern detection)\n"
            "- LLM02: Insecure Output Handling (command injection, output sanitization)\n"
            "- LLM04: Model Denial of Service (rate limiting, token/cost budgets, timeouts)\n"
            "- LLM06: Sensitive Information Disclosure (secret management, PII redaction)\n"
            "- LLM07: Insecure Plugin Design (tool authorization, policy engine, least privilege)\n"
            "- LLM08: Excessive Agency (autonomy levels, human approval gates, kill switches)\n\n"
            "Also verify:\n"
            "- Authentication, authorization, and access control\n"
            "- Data encryption (TLS 1.3 in transit, encryption at rest)\n"
            "- SQL/XSS/CSRF/SSRF protections and security audit logging\n\n"
            "Finding severity:\n"
            "- CRITICAL: Exploitable vulnerability, blocks release\n"
            "- HIGH: Significant security gap, must fix before launch\n"
            "- MEDIUM: Security weakness, should address\n"
            "- LOW: Minor defense-in-depth improvement\n\n"
            "Be SPECIFIC about threats and ACTIONABLE in recommendations."
        )

    def _build_review_prompt(self, spec: dict[str, Any]) -> str:
        """Build user prompt with specification for security review."""
        reqs_text = "\n".join(
            f"- {r['id']}: {r['description']}" for r in spec["functional_requirements"]
        )

        security_attrs = spec.get("quality_attributes", {}).get("security", [])
        if isinstance(security_attrs, list):
            security_text = (
                "\n".join(f"- {s}" for s in security_attrs)
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
Identify security risks in this specification across OWASP LLM Top 10 (LLM01, LLM02,
LLM04, LLM06, LLM07, LLM08), authentication/authorization, encryption/PII, injection
defenses, secret management, audit logging, and rate limiting.

Respond in JSON format."""
