"""
Output quality validator for routed LLM calls supporting schema compliance,
required field checks, minimum length checks, and optional LLM-as-judge scoring (EVO-026).
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import jsonschema

from product_value_system.adapters.model_adapter import ModelResponse

JudgeCallable = Callable[[str, dict[str, Any]], Awaitable[Any] | Any]


@dataclass
class QualityValidationResult:
    """Outcome of validating a routed model's response quality."""

    passed: bool
    score: float  # 0.0 - 1.0
    reason: str
    method: str = "schema_compliance"


def _extract_json_block(text: str) -> Any:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    return json.loads(cleaned)


class QualityValidator:
    """Validate routed model responses before accepting output or triggering fallback."""

    def __init__(
        self,
        *,
        method: str = "schema_compliance",
        json_schema: dict[str, Any] | None = None,
        required_keys: Sequence[str] | None = None,
        min_length: int = 1,
        min_score: float = 0.7,
        judge_callable: JudgeCallable | None = None,
    ) -> None:
        self.method = method
        self.json_schema = json_schema
        self.required_keys = list(required_keys or [])
        self.min_length = max(0, int(min_length))
        self.min_score = float(min_score)
        self.judge_callable = judge_callable

    async def validate(
        self,
        response: ModelResponse,
        *,
        context: dict[str, Any] | None = None,
    ) -> QualityValidationResult:
        """Validate ``response`` using the configured quality validation strategy."""
        content = (response.content or "").strip()

        if len(content) < self.min_length and not response.tool_calls:
            return QualityValidationResult(
                passed=False,
                score=0.0,
                reason=f"Response too short ({len(content)} chars < min {self.min_length}).",
                method=self.method,
            )

        if self.method == "llm_judge" and self.judge_callable is not None:
            return await self._validate_with_judge(content, context or {})

        if self.json_schema is not None or self.required_keys:
            return self._validate_schema_compliance(content)

        if response.finish_reason == "length":
            return QualityValidationResult(
                passed=False,
                score=0.4,
                reason="Response truncated due to max_tokens limit (finish_reason='length').",
                method=self.method,
            )

        return QualityValidationResult(
            passed=True,
            score=1.0,
            reason="Response passed baseline quality checks.",
            method=self.method,
        )

    def _validate_schema_compliance(self, content: str) -> QualityValidationResult:
        try:
            parsed = _extract_json_block(content)
        except Exception as exc:
            return QualityValidationResult(
                passed=False,
                score=0.0,
                reason=f"Response is not valid JSON: {exc}",
                method="schema_compliance",
            )

        if self.json_schema is not None:
            try:
                jsonschema.validate(instance=parsed, schema=self.json_schema)
            except jsonschema.ValidationError as exc:
                return QualityValidationResult(
                    passed=False,
                    score=0.2,
                    reason=f"JSON Schema validation failed: {exc.message}",
                    method="schema_compliance",
                )

        if self.required_keys:
            if not isinstance(parsed, dict):
                return QualityValidationResult(
                    passed=False,
                    score=0.1,
                    reason="Expected JSON object containing required keys.",
                    method="schema_compliance",
                )
            missing = [k for k in self.required_keys if k not in parsed]
            if missing:
                return QualityValidationResult(
                    passed=False,
                    score=0.3,
                    reason=f"Missing required JSON keys: {', '.join(missing)}",
                    method="schema_compliance",
                )

        return QualityValidationResult(
            passed=True,
            score=1.0,
            reason="Response complies with JSON schema requirements.",
            method="schema_compliance",
        )

    async def _validate_with_judge(
        self,
        content: str,
        context: dict[str, Any],
    ) -> QualityValidationResult:
        assert self.judge_callable is not None
        raw = self.judge_callable(content, context)
        result = await raw if inspect.isawaitable(raw) else raw

        if isinstance(result, QualityValidationResult):
            return result
        if isinstance(result, bool):
            return QualityValidationResult(
                passed=result,
                score=1.0 if result else 0.0,
                reason="LLM judge passed." if result else "LLM judge rejected response.",
                method="llm_judge",
            )
        if isinstance(result, (int, float)):
            score = max(0.0, min(1.0, float(result)))
            passed = score >= self.min_score
            return QualityValidationResult(
                passed=passed,
                score=score,
                reason=f"LLM judge score {score:.2f} (threshold {self.min_score:.2f}).",
                method="llm_judge",
            )
        if isinstance(result, dict):
            score = max(0.0, min(1.0, float(result.get("score", 0.0))))
            passed = bool(result.get("passed", score >= self.min_score))
            reason = str(result.get("reason") or f"LLM judge score {score:.2f}.")
            return QualityValidationResult(
                passed=passed,
                score=score,
                reason=reason,
                method="llm_judge",
            )
        return QualityValidationResult(
            passed=False,
            score=0.0,
            reason=f"Unsupported judge output type: {type(result).__name__}",
            method="llm_judge",
        )
