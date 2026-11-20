"""
Base independent review agent implementing the Elephant-Goldfish pattern (EVO-013).

All specialized review agents subclass ``BaseReviewAgent`` to guarantee:
1. Cleared memory before every review (Goldfish isolation)
2. Schema validation of input specifications and output evidence artifacts
3. Flexible parsing of both JSON object and JSON array LLM responses
4. Structured ``ReviewFinding`` normalization
"""

import json
import re
from datetime import UTC, datetime
from typing import Any

from product_value_system.agents.base_agent import BaseAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.tracing import traced
from product_value_system.review.models import (
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
)


class BaseReviewAgent(BaseAgent):
    """
    Reusable base class for independent specification review agents.

    Subclasses define ``review_type``, ``default_category``,
    ``_build_system_prompt()``, and ``_build_review_prompt(spec)``.
    """

    review_type: str = "review"
    evidence_type: str = "review"
    default_category: FindingCategory = FindingCategory.ARCHITECTURE

    def __init__(
        self,
        schema_validator: SchemaValidator,
        *,
        name: str,
        version: str = "0.1.0",
        capabilities: list[str] | None = None,
        required_tools: list[str] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 3000,
    ) -> None:
        super().__init__(
            name=name,
            version=version,
            capabilities=capabilities or [f"{self.review_type}_review"],
            required_tools=required_tools or ["schema.validate"],
            input_schema="https://pvs.ai/schemas/specification.schema.json",
            output_schema="https://pvs.ai/schemas/evidence.schema.json",
        )
        self.schema_validator = schema_validator
        self.temperature = temperature
        self.max_tokens = max_tokens

    @traced("review_agent.execute")
    async def execute(  # type: ignore[override]
        self,
        specification_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> dict[str, Any]:
        """
        Execute an independent review in a cleared Goldfish memory context.
        """
        spec = self._load_specification(specification_artifact)
        self.schema_validator.validate(spec, "specification.schema.json")

        # Elephant-Goldfish pattern: clear prior conversation messages & artifacts
        harness.memory.clear()

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_review_prompt(spec)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await harness.invoke_model(
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

        evidence = self._parse_review_response(
            response.content,
            spec,
            review_type=self.review_type,
        )

        self.schema_validator.validate(evidence, "evidence.schema.json")
        return evidence

    def _load_specification(self, input_artifact: dict[str, Any]) -> dict[str, Any]:
        """Unwrap specification dict if nested under ``spec`` or ``specification``."""
        if isinstance(input_artifact.get("spec"), dict):
            return dict(input_artifact["spec"])
        if isinstance(input_artifact.get("specification"), dict):
            return dict(input_artifact["specification"])
        return input_artifact

    def _build_system_prompt(self) -> str:
        """Build domain-specific system prompt (override in subclasses)."""
        raise NotImplementedError("Subclasses must implement _build_system_prompt()")

    def _build_review_prompt(self, spec: dict[str, Any]) -> str:
        """Build specification review prompt (override in subclasses)."""
        raise NotImplementedError("Subclasses must implement _build_review_prompt()")

    def _extract_json_payload(self, response_content: str) -> Any:
        """Extract JSON object or array from raw or Markdown-fenced LLM output."""
        content = response_content.strip()
        if "```json" in content:
            content = content.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in content:
            content = content.split("```", 1)[1].split("```", 1)[0].strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"(\[.*\]|\{.*\})", content, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise ValueError(f"Could not parse review JSON from response: {content[:200]}")

    def _derive_verdict(self, findings: list[ReviewFinding]) -> str:
        """Compute schema-valid verdict from parsed findings when not explicitly returned."""
        if any(f.severity == FindingSeverity.CRITICAL for f in findings):
            return "reject"
        if any(f.severity in (FindingSeverity.HIGH, FindingSeverity.MEDIUM) for f in findings):
            return "approve_with_concerns"
        return "approve"

    def _parse_review_response(
        self,
        response_content: str,
        specification: dict[str, Any],
        review_type: str,
    ) -> dict[str, Any]:
        """
        Parse LLM JSON response (dict with ``findings``/``overall_verdict`` or bare
        findings array) into a schema-valid ``evidence.schema.json`` artifact.
        """
        raw_payload = self._extract_json_payload(response_content)
        spec_id = str(specification["id"])
        evidence_id = f"{spec_id}-{review_type}-review"
        now = datetime.now(UTC).isoformat()

        if isinstance(raw_payload, list):
            raw_findings = raw_payload
            explicit_verdict = None
            summary = (
                f"{review_type.capitalize()} review completed with "
                f"{len(raw_findings)} finding(s)"
            )
        elif isinstance(raw_payload, dict):
            raw_findings = list(raw_payload.get("findings", []))
            explicit_verdict = raw_payload.get("overall_verdict") or raw_payload.get("verdict")
            default_summary = (
                f"{review_type.capitalize()} review completed with "
                f"{len(raw_findings)} finding(s)"
            )
            summary = str(raw_payload.get("summary") or default_summary)
        else:
            raise ValueError(f"Unexpected review payload type: {type(raw_payload)}")

        normalized_findings = [
            ReviewFinding.from_dict(
                item,
                default_reviewer=self.name,
                default_category=self.default_category,
            )
            for item in raw_findings
            if isinstance(item, dict)
        ]

        verdict = (
            str(explicit_verdict).lower()
            if explicit_verdict is not None
            else self._derive_verdict(normalized_findings)
        )

        # Preserve original schema-valid finding fields when supplied in canonical format,
        # while guaranteeing required schema keys for array-format EVO-013 responses.
        valid_sevs = {"critical", "high", "medium", "low"}
        evidence_findings: list[dict[str, Any]] = []
        for raw_item, norm in zip(raw_findings, normalized_findings, strict=False):
            if (
                isinstance(raw_item, dict)
                and "title" in raw_item
                and str(raw_item.get("severity", "")).lower() in valid_sevs
            ):
                entry: dict[str, Any] = {
                    "severity": str(raw_item["severity"]).lower(),
                    "category": str(raw_item.get("category", norm.category.value)),
                    "title": str(raw_item["title"]),
                    "description": str(raw_item["description"]),
                    "recommendation": str(raw_item["recommendation"]),
                }
                if "references" in raw_item and isinstance(raw_item["references"], list):
                    entry["references"] = [str(r) for r in raw_item["references"]]
                elif norm.requirement_id:
                    entry["references"] = [norm.requirement_id]
                evidence_findings.append(entry)
            else:
                evidence_findings.append(norm.to_evidence_finding())

        return {
            "id": evidence_id,
            "version": 1,
            "created": now,
            "type": self.evidence_type,
            "artifact_id": spec_id,
            "verdict": verdict,
            "summary": summary,
            "findings": evidence_findings,
            "reviewer": f"{self.name} v{self.version}",
        }
