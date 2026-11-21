"""
Unit tests for OpenTelemetry GenAI Semantic Conventions (v1.28+) tracer & spans (EVO-021).
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.adapters.model_adapter import (
    Message,
    ModelResponse,
    Tool,
    ToolCall,
    Usage,
)
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.genai_conventions import (
    GenAIAttributes,
    GenAIEvents,
    GenAIOperation,
    infer_genai_system,
    instrument_model_call,
)
from product_value_system.observability.genai_tracer import GenAITracer


@pytest.fixture
def isolated_genai_tracer() -> (
    Generator[tuple[GenAITracer, trace.Tracer, InMemorySpanExporter], None, None]
):
    """Create an isolated TracerProvider + InMemorySpanExporter without mutating global state."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.genai")
    genai_tracer = GenAITracer(tracer)

    yield genai_tracer, tracer, exporter

    exporter.clear()
    provider.shutdown()


@pytest.mark.unit
def test_chat_completion_standard_attributes(
    isolated_genai_tracer: tuple[GenAITracer, trace.Tracer, InMemorySpanExporter],
) -> None:
    """ChatCompletionSpan emits all standard OTEL GenAI v1.28+ attributes."""
    genai_tracer, _, exporter = isolated_genai_tracer

    with genai_tracer.trace_chat_completion(
        operation_name="chat.hypothesis_generation",
        model="claude-sonnet-4",
        messages=[{"role": "user", "content": "Generate a hypothesis"}],
        temperature=0.3,
        max_tokens=1024,
    ) as span:
        span.record_completion(
            response_id="msg_abc123",
            completion="Async validation reduces latency by 40%.",
            prompt_tokens=120,
            completion_tokens=45,
            finish_reason="stop",
            response_model="claude-sonnet-4-20250514",
            cost_usd=0.001035,
        )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})

    assert spans[0].name == "chat.hypothesis_generation"
    assert attrs[GenAIAttributes.SYSTEM] == "anthropic"
    assert attrs[GenAIAttributes.REQUEST_MODEL] == "claude-sonnet-4"
    assert attrs[GenAIAttributes.RESPONSE_MODEL] == "claude-sonnet-4-20250514"
    assert attrs[GenAIAttributes.OPERATION_NAME] == GenAIOperation.CHAT.value
    assert attrs[GenAIAttributes.REQUEST_TEMPERATURE] == 0.3
    assert attrs[GenAIAttributes.REQUEST_MAX_TOKENS] == 1024
    assert attrs[GenAIAttributes.RESPONSE_ID] == "msg_abc123"
    assert attrs[GenAIAttributes.RESPONSE_FINISH_REASON] == "stop"
    assert attrs[GenAIAttributes.USAGE_PROMPT_TOKENS] == 120
    assert attrs[GenAIAttributes.USAGE_COMPLETION_TOKENS] == 45
    assert attrs[GenAIAttributes.USAGE_TOTAL_TOKENS] == 165
    # Backwards-compatible aliases
    assert attrs[GenAIAttributes.USAGE_INPUT_TOKENS] == 120
    assert attrs[GenAIAttributes.USAGE_OUTPUT_TOKENS] == 45
    assert attrs[GenAIAttributes.COST_USD] == pytest.approx(0.001035)


@pytest.mark.unit
def test_chat_completion_optional_request_params(
    isolated_genai_tracer: tuple[GenAITracer, trace.Tracer, InMemorySpanExporter],
) -> None:
    """Optional request parameters (top_p, penalties, stop_sequences, tool counts) are recorded."""
    genai_tracer, _, exporter = isolated_genai_tracer

    with genai_tracer.trace_chat_completion(
        operation_name="chat.params_test",
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello"}],
        top_p=0.95,
        presence_penalty=0.2,
        frequency_penalty=0.1,
        stop_sequences=["END", "STOP"],
        tool_call_count=3,
    ) as span:
        span.record_completion(
            response_id="chatcmpl-999",
            completion="Done",
            prompt_tokens=10,
            completion_tokens=2,
            finish_reason="tool_calls",
            tool_call_count=1,
        )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})

    assert attrs[GenAIAttributes.SYSTEM] == "openai"
    assert attrs[GenAIAttributes.REQUEST_TOP_P] == 0.95
    assert attrs[GenAIAttributes.REQUEST_PRESENCE_PENALTY] == 0.2
    assert attrs[GenAIAttributes.REQUEST_FREQUENCY_PENALTY] == 0.1
    assert tuple(attrs[GenAIAttributes.REQUEST_STOP_SEQUENCES]) == ("END", "STOP")
    assert attrs[GenAIAttributes.REQUEST_TOOL_CALL_COUNT] == 3
    assert attrs[GenAIAttributes.RESPONSE_TOOL_CALL_COUNT] == 1
    assert attrs[GenAIAttributes.RESPONSE_FINISH_REASON] == "tool_calls"


@pytest.mark.unit
def test_chat_completion_prompt_and_completion_events(
    isolated_genai_tracer: tuple[GenAITracer, trace.Tracer, InMemorySpanExporter],
) -> None:
    """Standard gen_ai.content.prompt and gen_ai.content.completion events are emitted."""
    genai_tracer, _, exporter = isolated_genai_tracer

    messages = [
        {"role": "system", "content": "You are a product strategist."},
        {"role": "user", "content": "Analyze checkout drop-off."},
    ]
    with genai_tracer.trace_chat_completion(
        operation_name="chat.events",
        model="claude-sonnet-4",
        messages=messages,
    ) as span:
        span.record_completion(
            response_id="msg_ev1",
            completion="Drop-off occurs at payment verification.",
            prompt_tokens=30,
            completion_tokens=12,
        )

    spans = exporter.get_finished_spans()
    events = {event.name: dict(event.attributes or {}) for event in spans[0].events}

    assert GenAIEvents.CONTENT_PROMPT in events
    prompt_event = events[GenAIEvents.CONTENT_PROMPT]
    assert prompt_event["gen_ai.prompt.0.role"] == "system"
    assert prompt_event["gen_ai.prompt.0.content"] == "You are a product strategist."
    assert prompt_event["gen_ai.prompt.1.role"] == "user"
    assert prompt_event["gen_ai.prompt.1.content"] == "Analyze checkout drop-off."

    assert GenAIEvents.CONTENT_COMPLETION in events
    assert (
        events[GenAIEvents.CONTENT_COMPLETION]["gen_ai.completion"]
        == "Drop-off occurs at payment verification."
    )


@pytest.mark.unit
def test_tool_call_and_tool_result_events(
    isolated_genai_tracer: tuple[GenAITracer, trace.Tracer, InMemorySpanExporter],
) -> None:
    """ChatCompletionSpan emits standard gen_ai.tool.call and gen_ai.tool.result events."""
    genai_tracer, _, exporter = isolated_genai_tracer

    with genai_tracer.trace_chat_completion(
        operation_name="chat.tools",
        model="claude-sonnet-4",
        messages=[{"role": "user", "content": "Query metrics"}],
    ) as span:
        span.record_tool_call("query_metrics", '{"metric": "checkout_latency"}')
        span.record_tool_result("query_metrics", '{"p95_ms": 210}')
        span.record_completion(
            response_id="msg_tool1",
            completion="Current p95 latency is 210ms.",
            prompt_tokens=50,
            completion_tokens=15,
            finish_reason="stop",
        )

    spans = exporter.get_finished_spans()
    event_names = [e.name for e in spans[0].events]
    assert event_names == [
        GenAIEvents.CONTENT_PROMPT,
        GenAIEvents.TOOL_CALL,
        GenAIEvents.TOOL_RESULT,
        GenAIEvents.CONTENT_COMPLETION,
    ]

    tool_call_attrs = dict(spans[0].events[1].attributes or {})
    assert tool_call_attrs["gen_ai.tool.name"] == "query_metrics"
    assert tool_call_attrs["gen_ai.tool.input"] == '{"metric": "checkout_latency"}'

    tool_result_attrs = dict(spans[0].events[2].attributes or {})
    assert tool_result_attrs["gen_ai.tool.name"] == "query_metrics"
    assert tool_result_attrs["gen_ai.tool.output"] == '{"p95_ms": 210}'


@pytest.mark.unit
def test_text_completion_operation_span(
    isolated_genai_tracer: tuple[GenAITracer, trace.Tracer, InMemorySpanExporter],
) -> None:
    """TextCompletionSpan records completion operation name and prompt/completion events."""
    genai_tracer, _, exporter = isolated_genai_tracer

    with genai_tracer.trace_text_completion(
        operation_name="completion.summarize",
        model="davinci-002",
        prompt="Summarize: PIX instant payments",
        temperature=0.2,
        max_tokens=128,
    ) as span:
        span.record_completion(
            response_id="cmpl-123",
            completion="PIX enables 24/7 instant settlement.",
            prompt_tokens=12,
            completion_tokens=8,
            finish_reason="stop",
            cost_usd=0.0002,
        )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs[GenAIAttributes.OPERATION_NAME] == GenAIOperation.COMPLETION.value
    assert attrs[GenAIAttributes.SYSTEM] == "openai"
    assert attrs[GenAIAttributes.REQUEST_MAX_TOKENS] == 128
    assert attrs[GenAIAttributes.USAGE_TOTAL_TOKENS] == 20
    assert attrs[GenAIAttributes.COST_USD] == pytest.approx(0.0002)

    events = {e.name: dict(e.attributes or {}) for e in spans[0].events}
    assert events[GenAIEvents.CONTENT_PROMPT]["gen_ai.prompt"] == "Summarize: PIX instant payments"
    assert (
        events[GenAIEvents.CONTENT_COMPLETION]["gen_ai.completion"]
        == "PIX enables 24/7 instant settlement."
    )


@pytest.mark.unit
def test_embedding_operation_span(
    isolated_genai_tracer: tuple[GenAITracer, trace.Tracer, InMemorySpanExporter],
) -> None:
    """EmbeddingSpan records gen_ai.operation.name='embedding', dimensions, and tokens."""
    genai_tracer, _, exporter = isolated_genai_tracer

    with genai_tracer.trace_embedding(
        operation_name="embed.adr_document",
        model="text-embedding-3-small",
        input_text="ADR-001: Use Cedar for policy evaluation.",
    ) as span:
        span.record_embedding(dimensions=1536, token_count=18, cost_usd=0.00001)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs[GenAIAttributes.SYSTEM] == "openai"
    assert attrs[GenAIAttributes.OPERATION_NAME] == GenAIOperation.EMBEDDING.value
    assert attrs[GenAIAttributes.EMBEDDING_DIMENSIONS] == 1536
    assert attrs[GenAIAttributes.USAGE_PROMPT_TOKENS] == 18
    assert attrs[GenAIAttributes.USAGE_TOTAL_TOKENS] == 18
    assert attrs[GenAIAttributes.COST_USD] == pytest.approx(0.00001)


@pytest.mark.unit
def test_rerank_operation_span(
    isolated_genai_tracer: tuple[GenAITracer, trace.Tracer, InMemorySpanExporter],
) -> None:
    """RerankSpan records gen_ai.operation.name='rerank', top_n, document_count, and usage."""
    genai_tracer, _, exporter = isolated_genai_tracer

    docs = ["doc-1: Cedar policies", "doc-2: OTEL GenAI", "doc-3: Feature flags"]
    with genai_tracer.trace_rerank(
        operation_name="rerank.knowledge_search",
        model="rerank-english-v3.0",
        query="OpenTelemetry conventions",
        documents=docs,
        top_n=2,
    ) as span:
        span.record_rerank(returned_count=2, token_count=42, cost_usd=0.0005)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs[GenAIAttributes.SYSTEM] == "cohere"
    assert attrs[GenAIAttributes.OPERATION_NAME] == GenAIOperation.RERANK.value
    assert attrs[GenAIAttributes.RERANK_DOCUMENT_COUNT] == 3
    assert attrs[GenAIAttributes.RERANK_TOP_N] == 2
    assert attrs[GenAIAttributes.USAGE_PROMPT_TOKENS] == 42
    assert attrs[GenAIAttributes.COST_USD] == pytest.approx(0.0005)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model", "expected_system"),
    [
        ("claude-sonnet-4", "anthropic"),
        ("claude-3-5-haiku-20241022", "anthropic"),
        ("gpt-4o", "openai"),
        ("o3-mini", "openai"),
        ("text-embedding-3-large", "openai"),
        ("azure-gpt-4o-deployment", "azure_openai"),
        ("gemini-2.5-pro", "vertex_ai"),
        ("voyage-3-large", "voyage"),
        ("rerank-english-v3.0", "cohere"),
        ("command-r-plus", "cohere"),
        ("mock-model", "mock"),
        ("custom-local-llm", "unknown"),
    ],
)
def test_infer_genai_system_across_providers(model: str, expected_system: str) -> None:
    """infer_genai_system maps provider model prefixes to standard OTEL system identifiers."""
    assert infer_genai_system(model) == expected_system


@pytest.mark.unit
def test_span_error_handling_sets_error_status_and_type(
    isolated_genai_tracer: tuple[GenAITracer, trace.Tracer, InMemorySpanExporter],
) -> None:
    """Exceptions inside a GenAI span set StatusCode.ERROR, error.type, and exception event."""
    genai_tracer, _, exporter = isolated_genai_tracer

    with (
        pytest.raises(RuntimeError, match="Rate limit exceeded"),
        genai_tracer.trace_chat_completion(
            operation_name="chat.failing_call",
            model="claude-sonnet-4",
            messages=[{"role": "user", "content": "Trigger error"}],
        ),
    ):
        raise RuntimeError("Rate limit exceeded")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    attrs = dict(spans[0].attributes or {})
    assert attrs["error.type"] == "RuntimeError"
    event_names = [e.name for e in spans[0].events]
    assert "exception" in event_names


@pytest.mark.unit
def test_nested_genai_spans_preserve_parent_child_hierarchy(
    isolated_genai_tracer: tuple[GenAITracer, trace.Tracer, InMemorySpanExporter],
) -> None:
    """Nested GenAI spans attach context cleanly so child spans link to parent span ID."""
    genai_tracer, tracer, exporter = isolated_genai_tracer

    with (
        tracer.start_as_current_span("agent.discovery_workflow") as root_span,
        genai_tracer.trace_chat_completion(
            operation_name="chat.parent",
            model="claude-sonnet-4",
            messages=[{"role": "user", "content": "Plan research"}],
        ) as chat_span,
    ):
        with genai_tracer.trace_embedding(
            operation_name="embed.child",
            model="text-embedding-3-small",
            input_text="Research note",
        ) as emb_span:
            emb_span.record_embedding(dimensions=512, token_count=4)
        chat_span.record_completion(
            response_id="msg_nested",
            completion="Done",
            prompt_tokens=10,
            completion_tokens=3,
        )

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert set(spans.keys()) == {"agent.discovery_workflow", "chat.parent", "embed.child"}
    root_ctx = root_span.get_span_context()
    assert spans["chat.parent"].parent is not None
    assert spans["chat.parent"].parent.span_id == root_ctx.span_id
    assert spans["embed.child"].parent is not None
    assert spans["embed.child"].parent.span_id == spans["chat.parent"].context.span_id


@pytest.mark.unit
def test_sanitize_content_redacts_secrets_and_pii_in_events() -> None:
    """When sanitize_content=True, PII/secrets in prompt and completion events are redacted."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    genai_tracer = GenAITracer(provider.get_tracer("test.sanitized"), sanitize_content=True)

    with genai_tracer.trace_chat_completion(
        operation_name="chat.sanitized",
        model="claude-sonnet-4",
        messages=[{"role": "user", "content": "Contact alice@example.com with sk-ant-1234567890"}],
    ) as span:
        span.record_completion(
            response_id="msg_san",
            completion="Sent email to bob@corp.org",
            prompt_tokens=12,
            completion_tokens=6,
        )

    spans = exporter.get_finished_spans()
    events = {e.name: dict(e.attributes or {}) for e in spans[0].events}
    prompt_content = str(events[GenAIEvents.CONTENT_PROMPT]["gen_ai.prompt.0.content"])
    completion_content = str(events[GenAIEvents.CONTENT_COMPLETION]["gen_ai.completion"])

    assert "alice@example.com" not in prompt_content
    assert "bob@corp.org" not in completion_content
    provider.shutdown()


@pytest.mark.unit
def test_instrument_model_call_populates_v128_and_legacy_token_aliases(
    isolated_genai_tracer: tuple[GenAITracer, trace.Tracer, InMemorySpanExporter],
) -> None:
    """instrument_model_call sets v1.28+ prompt/completion/total and input/output aliases."""
    _, tracer, exporter = isolated_genai_tracer

    with tracer.start_as_current_span("manual.model_call") as span:
        instrument_model_call(
            span=span,
            system="anthropic",
            model="claude-sonnet-4",
            operation="chat",
            input_tokens=200,
            output_tokens=80,
            finish_reason="end_turn",
            response_id="msg_legacy_compat",
            cost_usd=0.0018,
            response_model="claude-sonnet-4-20250514",
        )

    spans = exporter.get_finished_spans()
    attrs = dict(spans[0].attributes or {})
    assert attrs[GenAIAttributes.USAGE_PROMPT_TOKENS] == 200
    assert attrs[GenAIAttributes.USAGE_COMPLETION_TOKENS] == 80
    assert attrs[GenAIAttributes.USAGE_TOTAL_TOKENS] == 280
    assert attrs[GenAIAttributes.USAGE_INPUT_TOKENS] == 200
    assert attrs[GenAIAttributes.USAGE_OUTPUT_TOKENS] == 80
    assert attrs[GenAIAttributes.RESPONSE_MODEL] == "claude-sonnet-4-20250514"
    assert attrs[GenAIAttributes.COST_USD] == pytest.approx(0.0018)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_harness_invoke_model_emits_standard_genai_span_and_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Harness.invoke_model wraps model calls with compliant GenAI v1.28+ spans and events."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)

    adapter = MockModelAdapter(responses=["Experiment hypothesis looks statistically sound."])
    harness = Harness(
        context=ExecutionContext(
            run_id="run-otel-harness",
            stage_name="discovery",
            agent_name="discovery-agent",
        ),
        model_adapter=adapter,
    )
    harness.budget.start()

    response = await harness.invoke_model(
        messages=[{"role": "user", "content": "Evaluate this checkout hypothesis"}],
        temperature=0.4,
        max_tokens=512,
    )

    assert response.content == "Experiment hypothesis looks statistically sound."
    spans = [
        s
        for s in exporter.get_finished_spans()
        if s.name == "harness.invoke_model.discovery-agent"
    ]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs[GenAIAttributes.OPERATION_NAME] == "chat"
    assert attrs[GenAIAttributes.RESPONSE_MODEL] == "mock-model"
    assert attrs[GenAIAttributes.REQUEST_TEMPERATURE] == 0.4
    assert attrs[GenAIAttributes.REQUEST_MAX_TOKENS] == 512
    assert attrs[GenAIAttributes.USAGE_TOTAL_TOKENS] == response.usage.total_tokens  # type: ignore[union-attr]
    event_names = [e.name for e in spans[0].events]
    assert GenAIEvents.CONTENT_PROMPT in event_names
    assert GenAIEvents.CONTENT_COMPLETION in event_names
    provider.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_harness_invoke_model_records_tool_calls_in_genai_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Harness.invoke_model emits gen_ai.tool.call events when the model requests tools."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)

    class _ToolCallingAdapter:
        model = "claude-sonnet-4"

        async def invoke(
            self,
            messages: list[Message],
            tools: list[Tool] | None = None,
            temperature: float = 0.7,
            max_tokens: int | None = None,
            **kwargs: Any,
        ) -> ModelResponse:
            return ModelResponse(
                content="Calling baseline validator.",
                model="claude-sonnet-4",
                usage=Usage(input_tokens=40, output_tokens=15, total_tokens=55),
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="validate_baseline",
                        arguments={"metric": "conversion_rate"},
                    )
                ],
            )

    harness = Harness(
        context=ExecutionContext(
            run_id="run-otel-tools",
            stage_name="experiment",
            agent_name="experiment-agent",
        ),
        model_adapter=_ToolCallingAdapter(),  # type: ignore[arg-type]
    )
    harness.budget.start()

    await harness.invoke_model(
        messages=[{"role": "user", "content": "Check baseline quality"}],
        tools=[Tool(name="validate_baseline", description="Validate baseline", parameters={})],
    )

    spans = [
        s
        for s in exporter.get_finished_spans()
        if s.name == "harness.invoke_model.experiment-agent"
    ]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs[GenAIAttributes.SYSTEM] == "anthropic"
    assert attrs[GenAIAttributes.REQUEST_TOOL_CALL_COUNT] == 1
    assert attrs[GenAIAttributes.RESPONSE_TOOL_CALL_COUNT] == 1

    tool_events = [e for e in spans[0].events if e.name == GenAIEvents.TOOL_CALL]
    assert len(tool_events) == 1
    assert dict(tool_events[0].attributes or {})["gen_ai.tool.name"] == "validate_baseline"
    provider.shutdown()
