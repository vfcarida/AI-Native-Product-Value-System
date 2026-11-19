"""
AI-based judges (quality, completeness, clarity).
"""

import json
from typing import Any

from product_value_system.adapters.model_adapter import Message, ModelAdapter, Role
from product_value_system.evaluation.judge import Judge, JudgeResult


class CompletenessJudge(Judge):
    """Evaluates completeness of specifications."""

    def __init__(self, model_adapter: ModelAdapter) -> None:
        self.model_adapter = model_adapter

    @property
    def name(self) -> str:
        return "completeness"

    @property
    def judge_type(self) -> str:
        return "ai_based"

    async def evaluate(
        self,
        artifact: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> JudgeResult:
        """Evaluate completeness of artifact."""
        prompt = self._build_evaluation_prompt(artifact, context)

        response = await self.model_adapter.invoke(
            messages=[
                Message(
                    role=Role.SYSTEM,
                    content="You are an expert evaluator of technical specifications.",
                ),
                Message(role=Role.USER, content=prompt),
            ],
            temperature=0.0,
            max_tokens=500,
        )

        result = self._parse_response(response.content)
        score = float(result["score"])

        return JudgeResult(
            judge_name=self.name,
            artifact_type=str(artifact.get("type", "unknown")),
            score=score,
            passed=score >= 0.7,
            rationale=str(result["rationale"]),
        )

    def _build_evaluation_prompt(
        self,
        artifact: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> str:
        """Build evaluation prompt."""
        return f"""Evaluate this artifact for COMPLETENESS:

## Artifact
{json.dumps(artifact, indent=2)}

## Context
{json.dumps(context or {}, indent=2)}

Rate completeness on scale 0.0 to 1.0:
- 1.0: All essential elements present, no gaps
- 0.7: Mostly complete, minor gaps
- 0.4: Significant gaps or missing elements
- 0.0: Incomplete, cannot proceed

Respond in JSON format:
{{
  "score": 0.85,
  "rationale": "Brief explanation of score"
}}"""

    def _parse_response(self, response: str) -> dict[str, Any]:
        """Parse model response."""
        content = response.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        parsed: dict[str, Any] = json.loads(content)
        return parsed


class ClarityJudge(Judge):
    """Evaluates clarity and readability."""

    def __init__(self, model_adapter: ModelAdapter) -> None:
        self.model_adapter = model_adapter

    @property
    def name(self) -> str:
        return "clarity"

    @property
    def judge_type(self) -> str:
        return "ai_based"

    async def evaluate(
        self,
        artifact: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> JudgeResult:
        """Evaluate clarity of artifact."""
        prompt = f"""Evaluate this artifact for CLARITY:

## Artifact
{json.dumps(artifact, indent=2)}

Rate clarity on scale 0.0 to 1.0:
- 1.0: Clear, unambiguous, well-structured
- 0.7: Mostly clear, minor ambiguities
- 0.4: Significant ambiguities or poor structure
- 0.0: Unclear, confusing

Respond in JSON format:
{{
  "score": 0.80,
  "rationale": "Brief explanation"
}}"""

        response = await self.model_adapter.invoke(
            messages=[
                Message(role=Role.SYSTEM, content="You are an expert evaluator."),
                Message(role=Role.USER, content=prompt),
            ],
            temperature=0.0,
            max_tokens=500,
        )

        content = response.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        result: dict[str, Any] = json.loads(content)
        score = float(result["score"])

        return JudgeResult(
            judge_name=self.name,
            artifact_type=str(artifact.get("type", "unknown")),
            score=score,
            passed=score >= 0.7,
            rationale=str(result["rationale"]),
        )
