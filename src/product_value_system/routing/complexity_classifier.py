"""
Task complexity classifier for cost-effective LLM model routing (EVO-026).

Scores prompts along 6 features (input token length, task keywords, schema-driven
determinism, creativity indicators, reasoning depth, and code complexity) and maps
them into LOW, MEDIUM, or HIGH complexity tiers.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from product_value_system.adapters.model_adapter import Message


class ComplexityLevel(str, Enum):
    """Task complexity tier governing model selection."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class ComplexityScore:
    """Task complexity assessment with score, tier, feature vector, and explanation."""

    level: ComplexityLevel
    score: float  # 0.0 - 1.0
    reasoning: str
    features: dict[str, float]
    token_count: int = 0


_TASK_TYPE_HINTS: dict[str, ComplexityLevel] = {
    "format_conversion": ComplexityLevel.LOW,
    "extract_functions": ComplexityLevel.LOW,
    "extraction": ComplexityLevel.LOW,
    "template_fill": ComplexityLevel.LOW,
    "schema_validation": ComplexityLevel.LOW,
    "summarization": ComplexityLevel.LOW,
    "code_analysis": ComplexityLevel.MEDIUM,
    "test_generation": ComplexityLevel.MEDIUM,
    "spec_generation": ComplexityLevel.MEDIUM,
    "documentation": ComplexityLevel.MEDIUM,
    "code_review": ComplexityLevel.MEDIUM,
    "ideation": ComplexityLevel.HIGH,
    "architecture_design": ComplexityLevel.HIGH,
    "strategic_decision": ComplexityLevel.HIGH,
    "root_cause_analysis": ComplexityLevel.HIGH,
    "modernization_strategy": ComplexityLevel.HIGH,
}


def _count_tokens(text: str, tokenizer: Any | None) -> int:
    if not text:
        return 0
    if tokenizer is not None:
        try:
            return len(tokenizer.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def _extract_message_text(messages: Sequence[Message | dict[str, Any]]) -> str:
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, Message):
            parts.append(msg.content)
        else:
            parts.append(str(msg.get("content", "")))
    return " ".join(p for p in parts if p)


def _count_keyword_hits(text_lower: str, keywords: Sequence[str]) -> int:
    hits = 0
    for kw in keywords:
        if re.search(rf"\b{re.escape(kw)}\w*\b", text_lower):
            hits += 1
    return hits


class ComplexityClassifier:
    """Heuristic + feature-weighted classifier mapping prompts to LOW/MEDIUM/HIGH tiers."""

    LOW_KEYWORDS: tuple[str, ...] = (
        "extract",
        "list",
        "format",
        "convert",
        "validate",
        "parse",
        "filter",
        "summarize",
        "count",
        "template",
        "rename",
    )
    MEDIUM_KEYWORDS: tuple[str, ...] = (
        "analyze",
        "compare",
        "explain",
        "refactor",
        "optimize",
        "test",
        "document",
        "classify",
        "specification",
        "review",
        "implement",
    )
    HIGH_KEYWORDS: tuple[str, ...] = (
        "design",
        "architect",
        "strategize",
        "strategy",
        "innovate",
        "create",
        "invent",
        "hypothesize",
        "plan",
        "moderniz",
        "root cause",
        "trade-off",
        "tradeoff",
    )
    CREATIVITY_INDICATORS: tuple[str, ...] = (
        "novel",
        "creative",
        "innovative",
        "original",
        "unique",
        "ideate",
        "brainstorm",
        "vision",
        "greenfield",
    )
    REASONING_INDICATORS: tuple[str, ...] = (
        "because",
        "therefore",
        "considering",
        "trade-off",
        "tradeoff",
        "root cause",
        "multi-step",
        "architectural",
        "strategic",
        "diagnose",
        "evaluate",
        "analyze",
    )
    SCHEMA_INDICATORS: tuple[str, ...] = (
        "json",
        "yaml",
        "schema",
        "csv",
        "template",
        "fields",
        "strict format",
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.tokenizer: Any | None = None
        try:
            import tiktoken

            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self.tokenizer = None

        self.low_complexity_keywords = list(self.LOW_KEYWORDS)
        self.medium_complexity_keywords = list(self.MEDIUM_KEYWORDS)
        self.high_complexity_keywords = list(self.HIGH_KEYWORDS)

    def classify(
        self,
        messages: Sequence[Message | dict[str, Any]],
        task_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ComplexityScore:
        """Assess prompt complexity and return tier, normalized score, features & reasoning."""
        text = _extract_message_text(messages)
        token_count = _count_tokens(text, self.tokenizer)
        features = self._extract_features(messages, task_type, metadata, token_count=token_count)
        score = self._compute_score(features)

        if score < 0.33:
            level = ComplexityLevel.LOW
        elif score < 0.67:
            level = ComplexityLevel.MEDIUM
        else:
            level = ComplexityLevel.HIGH

        # Respect explicit task_type hint when no strong conflicting complexity signals exist
        if task_type:
            hint = _TASK_TYPE_HINTS.get(task_type.strip().lower())
            if hint == ComplexityLevel.HIGH and level != ComplexityLevel.HIGH:
                level = ComplexityLevel.HIGH
                score = max(score, 0.72)
            elif hint == ComplexityLevel.LOW and token_count <= 2000 and score < 0.5:
                level = ComplexityLevel.LOW
                score = min(score, 0.28)

        reasoning = self._generate_reasoning(features, score, level)
        return ComplexityScore(
            level=level,
            score=round(score, 4),
            reasoning=reasoning,
            features={k: round(v, 4) for k, v in features.items()},
            token_count=token_count,
        )

    def _extract_features(
        self,
        messages: Sequence[Message | dict[str, Any]],
        task_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        token_count: int | None = None,
    ) -> dict[str, float]:
        """Extract normalized numerical complexity features from prompt messages."""
        text = _extract_message_text(messages)
        tokens = token_count if token_count is not None else _count_tokens(text, self.tokenizer)
        input_length_feature = min(tokens / 8000.0, 1.0)

        text_lower = text.lower()
        low_count = _count_keyword_hits(text_lower, self.low_complexity_keywords)
        medium_count = _count_keyword_hits(text_lower, self.medium_complexity_keywords)
        high_count = _count_keyword_hits(text_lower, self.high_complexity_keywords)

        # Incorporate explicit task_type vocabulary if provided
        if task_type:
            tt = task_type.replace("_", " ").lower()
            low_count += _count_keyword_hits(tt, self.low_complexity_keywords)
            medium_count += _count_keyword_hits(tt, self.medium_complexity_keywords)
            high_count += _count_keyword_hits(tt, self.high_complexity_keywords)

        total_kw = low_count + medium_count + high_count
        if total_kw > 0:
            # High-complexity keywords carry elevated semantic weight in mixed prompts
            weighted_num = low_count * 0.10 + medium_count * 0.55 + high_count * 1.05
            keyword_score = min(1.0, weighted_num / total_kw)
        else:
            keyword_score = 0.50

        # Schema-driven deterministic output simplifies generation (1.0 when structured)
        has_schema = any(ind in text_lower for ind in self.SCHEMA_INDICATORS)
        schema_driven = 1.0 if has_schema else 0.0

        creativity_hits = sum(1 for ind in self.CREATIVITY_INDICATORS if ind in text_lower)
        creativity_feature = min(creativity_hits / 2.0, 1.0)

        reasoning_hits = sum(1 for ind in self.REASONING_INDICATORS if ind in text_lower)
        reasoning_feature = min(reasoning_hits / 2.0, 1.0)

        code_blocks = re.findall(r"```[\s\S]*?```", text)
        if code_blocks:
            avg_code_len = sum(len(block) for block in code_blocks) / len(code_blocks)
            code_complexity = min(avg_code_len / 800.0, 1.0)
        else:
            code_complexity = 0.0

        return {
            "input_length": input_length_feature,
            "keyword_score": keyword_score,
            "schema_driven": schema_driven,
            "creativity": creativity_feature,
            "reasoning_depth": reasoning_feature,
            "code_complexity": code_complexity,
        }

    @staticmethod
    def _compute_score(features: dict[str, float]) -> float:
        """Compute weighted complexity score in [0.0, 1.0]."""
        weights = {
            "input_length": 0.18,
            "keyword_score": 0.68,
            "schema_driven": -0.12,  # Schema-driven deterministic outputs are simpler
            "creativity": 0.18,
            "reasoning_depth": 0.18,
            "code_complexity": 0.15,
        }
        raw = sum(features[key] * weights[key] for key in weights)
        return max(0.0, min(1.0, raw))

    @staticmethod
    def _generate_reasoning(
        features: dict[str, float],
        score: float,
        level: ComplexityLevel,
    ) -> str:
        reasons: list[str] = []
        if features["input_length"] > 0.4:
            reasons.append("long input context")
        if features["keyword_score"] >= 0.75:
            reasons.append("high-complexity task type")
        elif features["keyword_score"] <= 0.30:
            reasons.append("low-complexity task type")
        else:
            reasons.append("medium-complexity task type")
        if features["schema_driven"] >= 0.5:
            reasons.append("structured output (schema-driven)")
        if features["creativity"] >= 0.5:
            reasons.append("creativity required")
        if features["reasoning_depth"] >= 0.5:
            reasons.append("multi-step reasoning")
        if features["code_complexity"] >= 0.5:
            reasons.append("complex code analysis")

        base = f"Classified as {level.value.upper()} complexity (score: {score:.2f})."
        if reasons:
            return f"{base} Factors: {', '.join(reasons)}."
        return base
