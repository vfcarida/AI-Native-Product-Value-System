"""
OpenTelemetry GenAI Semantic Conventions (v1.28+).

Reference: https://opentelemetry.io/docs/specs/semconv/gen-ai/
"""

from __future__ import annotations

from enum import Enum

from opentelemetry import trace


class GenAIOperation(str, Enum):
    """Standard OpenTelemetry GenAI operation types."""

    CHAT = "chat"
    COMPLETION = "completion"
    EMBEDDING = "embedding"
    RERANK = "rerank"


class GenAIEvents:
    """Standard OpenTelemetry GenAI span event names."""

    CONTENT_PROMPT = "gen_ai.content.prompt"
    CONTENT_COMPLETION = "gen_ai.content.completion"
    TOOL_CALL = "gen_ai.tool.call"
    TOOL_RESULT = "gen_ai.tool.result"


class GenAIAttributes:
    """OpenTelemetry GenAI v1.28+ semantic convention attribute names."""

    # System & Model
    SYSTEM = "gen_ai.system"  # anthropic, openai, azure_openai, vertex_ai, etc.
    REQUEST_MODEL = "gen_ai.request.model"
    RESPONSE_MODEL = "gen_ai.response.model"

    # Operation
    OPERATION_NAME = "gen_ai.operation.name"  # chat, completion, embedding, rerank

    # Request parameters
    REQUEST_TEMPERATURE = "gen_ai.request.temperature"
    REQUEST_TOP_P = "gen_ai.request.top_p"
    REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
    REQUEST_PRESENCE_PENALTY = "gen_ai.request.presence_penalty"
    REQUEST_FREQUENCY_PENALTY = "gen_ai.request.frequency_penalty"
    REQUEST_STOP_SEQUENCES = "gen_ai.request.stop_sequences"
    REQUEST_TOOL_CALL_COUNT = "gen_ai.request.tool_call_count"

    # Response metadata
    RESPONSE_ID = "gen_ai.response.id"
    RESPONSE_FINISH_REASON = "gen_ai.response.finish_reason"
    RESPONSE_TOOL_CALL_COUNT = "gen_ai.response.tool_call_count"

    # Token usage (v1.28+ prompt/completion/total + input/output compatibility)
    USAGE_PROMPT_TOKENS = "gen_ai.usage.prompt_tokens"
    USAGE_COMPLETION_TOKENS = "gen_ai.usage.completion_tokens"
    USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
    USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

    # Embedding & Rerank attributes
    EMBEDDING_DIMENSIONS = "gen_ai.embedding.dimensions"
    RERANK_TOP_N = "gen_ai.rerank.top_n"
    RERANK_DOCUMENT_COUNT = "gen_ai.rerank.document_count"

    # Cost & PVS extensions
    COST_USD = "gen_ai.usage.cost_usd"
    AGENT_NAME = "pvs.agent.name"
    STAGE_NAME = "pvs.stage.name"


def infer_genai_system(model: str) -> str:
    """Infer standard ``gen_ai.system`` provider identifier from ``model`` name."""
    lower = model.strip().lower()
    if "claude" in lower:
        return "anthropic"
    if "azure" in lower:
        return "azure_openai"
    if (
        lower.startswith(("gpt", "o1", "o3", "o4", "text-embedding", "davinci", "curie"))
        or "openai" in lower
    ):
        return "openai"
    if "gemini" in lower or "palm" in lower or "textembedding-gecko" in lower:
        return "vertex_ai"
    if "voyage" in lower:
        return "voyage"
    if "cohere" in lower or "rerank" in lower or "command-r" in lower:
        return "cohere"
    if "mock" in lower:
        return "mock"
    return "unknown"


def instrument_model_call(
    span: trace.Span,
    system: str,
    model: str,
    operation: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    finish_reason: str | None = None,
    response_id: str | None = None,
    cost_usd: float | None = None,
    response_model: str | None = None,
) -> None:
    """Attach standard OpenTelemetry GenAI semantic convention attributes to ``span``."""
    span.set_attribute(GenAIAttributes.SYSTEM, system)
    span.set_attribute(GenAIAttributes.REQUEST_MODEL, model)
    span.set_attribute(GenAIAttributes.RESPONSE_MODEL, response_model or model)
    span.set_attribute(GenAIAttributes.OPERATION_NAME, operation)

    if input_tokens is not None:
        span.set_attribute(GenAIAttributes.USAGE_INPUT_TOKENS, input_tokens)
        span.set_attribute(GenAIAttributes.USAGE_PROMPT_TOKENS, input_tokens)

    if output_tokens is not None:
        span.set_attribute(GenAIAttributes.USAGE_OUTPUT_TOKENS, output_tokens)
        span.set_attribute(GenAIAttributes.USAGE_COMPLETION_TOKENS, output_tokens)

    if input_tokens is not None and output_tokens is not None:
        span.set_attribute(GenAIAttributes.USAGE_TOTAL_TOKENS, input_tokens + output_tokens)

    if finish_reason:
        span.set_attribute(GenAIAttributes.RESPONSE_FINISH_REASON, finish_reason)

    if response_id:
        span.set_attribute(GenAIAttributes.RESPONSE_ID, response_id)

    if cost_usd is not None:
        span.set_attribute(GenAIAttributes.COST_USD, cost_usd)
