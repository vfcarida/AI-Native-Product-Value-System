"""
OpenTelemetry GenAI semantic conventions.

Based on: https://github.com/open-telemetry/semantic-conventions-genai
"""

from opentelemetry import trace


class GenAIAttributes:
    """GenAI semantic convention attribute names."""

    # Model
    SYSTEM = "gen_ai.system"  # e.g., "openai"
    REQUEST_MODEL = "gen_ai.request.model"  # e.g., "gpt-4"
    RESPONSE_MODEL = "gen_ai.response.model"  # Actual model used

    # Usage
    USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

    # Response
    RESPONSE_ID = "gen_ai.response.id"
    RESPONSE_FINISH_REASON = "gen_ai.response.finish_reason"

    # Operation
    OPERATION_NAME = "gen_ai.operation.name"  # e.g., "chat", "completion"

    # Custom (PVS-specific)
    COST_USD = "gen_ai.usage.cost_usd"
    AGENT_NAME = "pvs.agent.name"
    STAGE_NAME = "pvs.stage.name"


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
) -> None:
    """
    Add GenAI semantic convention attributes to a span.

    Args:
        span: Active span
        system: Model provider (openai, anthropic, google)
        model: Model identifier (gpt-4, claude-3-opus-20240229)
        operation: Operation type (chat, completion, embedding)
        input_tokens: Input token count
        output_tokens: Output token count
        finish_reason: Finish reason (stop, length, tool_calls)
        response_id: Provider's response ID
        cost_usd: Cost in USD
    """
    span.set_attribute(GenAIAttributes.SYSTEM, system)
    span.set_attribute(GenAIAttributes.REQUEST_MODEL, model)
    span.set_attribute(GenAIAttributes.OPERATION_NAME, operation)

    if input_tokens is not None:
        span.set_attribute(GenAIAttributes.USAGE_INPUT_TOKENS, input_tokens)

    if output_tokens is not None:
        span.set_attribute(GenAIAttributes.USAGE_OUTPUT_TOKENS, output_tokens)

    if finish_reason:
        span.set_attribute(GenAIAttributes.RESPONSE_FINISH_REASON, finish_reason)

    if response_id:
        span.set_attribute(GenAIAttributes.RESPONSE_ID, response_id)

    if cost_usd is not None:
        span.set_attribute(GenAIAttributes.COST_USD, cost_usd)
