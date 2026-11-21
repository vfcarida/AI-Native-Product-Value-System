"""
Backward-compatibility migration shim converting legacy ``llm.*`` attributes
to official OpenTelemetry GenAI ``gen_ai.*`` conventions (EVO-021).
"""

from __future__ import annotations

import warnings
from typing import Any

from product_value_system.observability.genai_tracer import ChatCompletionSpan, GenAITracer

ATTRIBUTE_MIGRATION_MAP: dict[str, str] = {
    "llm.model": "gen_ai.request.model",
    "llm.tokens": "gen_ai.usage.total_tokens",
    "llm.prompt_tokens": "gen_ai.usage.prompt_tokens",
    "llm.completion_tokens": "gen_ai.usage.completion_tokens",
    "llm.temperature": "gen_ai.request.temperature",
    "llm.max_tokens": "gen_ai.request.max_tokens",
    "llm.provider": "gen_ai.system",
    "llm.response_id": "gen_ai.response.id",
    "llm.finish_reason": "gen_ai.response.finish_reason",
    "llm.operation": "gen_ai.operation.name",
    "llm.cost": "gen_ai.usage.cost_usd",
}


def migrate_attributes(
    old_attributes: dict[str, Any],
    *,
    emit_warnings: bool = True,
) -> dict[str, Any]:
    """
    Translate deprecated ``llm.*`` keys into official ``gen_ai.*`` semantic conventions.

    If both a legacy key and its standard replacement exist in ``old_attributes``,
    the standard ``gen_ai.*`` value takes precedence.
    """
    migrated: dict[str, Any] = {}
    for key, value in old_attributes.items():
        if key in ATTRIBUTE_MIGRATION_MAP:
            new_key = ATTRIBUTE_MIGRATION_MAP[key]
            if emit_warnings:
                warnings.warn(
                    f"Attribute '{key}' is deprecated. Use '{new_key}' instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            if new_key not in old_attributes:
                migrated[new_key] = value
        else:
            migrated[key] = value
    return migrated


class CompatibilityTracer:
    """Drop-in wrapper accepting both legacy ``llm.*`` and standard ``gen_ai.*`` arguments."""

    def __init__(self, genai_tracer: GenAITracer) -> None:
        self.genai_tracer = genai_tracer

    def trace_llm_call(self, **kwargs: Any) -> ChatCompletionSpan:
        """Trace an LLM call accepting legacy ``llm.*`` or standard ``gen_ai.*`` keys."""
        migrated = migrate_attributes(kwargs)
        model = str(migrated.get("gen_ai.request.model") or migrated.get("model", "unknown"))
        messages = list(migrated.get("messages", []))
        temperature = float(
            migrated.get("gen_ai.request.temperature", migrated.get("temperature", 0.7))
        )
        max_tokens_raw = migrated.get("gen_ai.request.max_tokens", migrated.get("max_tokens"))
        max_tokens = int(max_tokens_raw) if max_tokens_raw is not None else None
        system_raw = migrated.get("gen_ai.system")
        system = str(system_raw) if system_raw is not None else None

        return self.genai_tracer.trace_chat_completion(
            operation_name=str(kwargs.get("operation_name", "llm_call")),
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            system=system,
        )
