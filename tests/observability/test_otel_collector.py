"""
Integration tests for OpenTelemetry GenAI Collector export pipeline & sanitization (EVO-021).
"""

from __future__ import annotations

import os

import pytest
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from product_value_system.observability.exporters import create_otlp_exporter
from product_value_system.observability.genai_conventions import GenAIAttributes, GenAIOperation
from product_value_system.observability.genai_metrics import GenAIMetrics
from product_value_system.observability.genai_tracer import GenAITracer
from product_value_system.observability.sanitized_tracing import SanitizingSpanProcessor


@pytest.mark.integration
def test_otlp_batch_export_pipeline_exports_compliant_genai_spans() -> None:
    """BatchSpanProcessor exports full chat, embedding, and rerank traces with resource metadata."""
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "product-value-system", "service.version": "0.2.0"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    metrics = GenAIMetrics()
    genai_tracer = GenAITracer(provider.get_tracer("pvs.pipeline"), metrics=metrics)

    with genai_tracer.trace_chat_completion(
        operation_name="pipeline.discovery",
        model="claude-sonnet-4",
        messages=[{"role": "user", "content": "Generate product outcome hypothesis"}],
    ) as chat_span:
        with genai_tracer.trace_embedding(
            operation_name="pipeline.embed",
            model="text-embedding-3-small",
            input_text="Customer feedback snippet",
        ) as emb_span:
            emb_span.record_embedding(dimensions=1536, token_count=12, cost_usd=0.0001)

        with genai_tracer.trace_rerank(
            operation_name="pipeline.rerank",
            model="rerank-english-v3.0",
            query="checkout friction",
            documents=["d1", "d2"],
            top_n=1,
        ) as rerank_span:
            rerank_span.record_rerank(returned_count=1, token_count=15, cost_usd=0.0002)

        chat_span.record_completion(
            response_id="msg_pipe_1",
            completion="Hypothesis generated.",
            prompt_tokens=100,
            completion_tokens=40,
            cost_usd=0.0009,
        )

    assert provider.force_flush() is True
    spans = exporter.get_finished_spans()
    assert len(spans) == 3

    ops = {dict(s.attributes or {})[GenAIAttributes.OPERATION_NAME] for s in spans}
    assert ops == {
        GenAIOperation.CHAT.value,
        GenAIOperation.EMBEDDING.value,
        GenAIOperation.RERANK.value,
    }
    # All spans belong to the same trace and carry service.name resource metadata
    trace_ids = {s.context.trace_id for s in spans}
    assert len(trace_ids) == 1
    assert all(s.resource.attributes["service.name"] == "product-value-system" for s in spans)
    provider.shutdown()


@pytest.mark.integration
def test_sanitizing_span_processor_with_genai_tracer() -> None:
    """SanitizingSpanProcessor chains cleanly with GenAITracer without stripping gen_ai.* attrs."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SanitizingSpanProcessor(SimpleSpanProcessor(exporter)))
    genai_tracer = GenAITracer(provider.get_tracer("pvs.sanitized.pipeline"), sanitize_content=True)

    with genai_tracer.trace_chat_completion(
        operation_name="sanitized.chat",
        model="claude-sonnet-4",
        messages=[{"role": "user", "content": "Review ADR-001"}],
    ) as span:
        span.record_completion(
            response_id="msg_clean",
            completion="Approved",
            prompt_tokens=20,
            completion_tokens=4,
            cost_usd=0.0001,
        )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs[GenAIAttributes.SYSTEM] == "anthropic"
    assert attrs[GenAIAttributes.OPERATION_NAME] == "chat"
    assert attrs[GenAIAttributes.USAGE_TOTAL_TOKENS] == 24
    provider.shutdown()


@pytest.mark.integration
def test_otlp_exporter_factory_creates_valid_exporter() -> None:
    """create_otlp_exporter constructs an OTLPSpanExporter bound to the collector endpoint."""
    exporter = create_otlp_exporter(endpoint="http://localhost:4317")
    assert isinstance(exporter, OTLPSpanExporter)
    exporter.shutdown()


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("OTEL_COLLECTOR_ENDPOINT"),
    reason="Requires live OTEL Collector (set OTEL_COLLECTOR_ENDPOINT to run)",
)
def test_live_otel_collector_export() -> None:
    """Export a live GenAI span to a running OpenTelemetry Collector when configured."""
    endpoint = os.environ["OTEL_COLLECTOR_ENDPOINT"]
    exporter = create_otlp_exporter(endpoint=endpoint)
    provider = TracerProvider(resource=Resource.create({"service.name": "pvs-live-test"}))
    provider.add_span_processor(BatchSpanProcessor(exporter))

    genai_tracer = GenAITracer(provider.get_tracer("pvs.live"))
    with genai_tracer.trace_chat_completion(
        operation_name="live.collector_probe",
        model="claude-sonnet-4",
        messages=[{"role": "user", "content": "Health probe"}],
    ) as span:
        span.record_completion(
            response_id="live-probe-1",
            completion="OK",
            prompt_tokens=5,
            completion_tokens=1,
        )

    assert provider.force_flush() is True
    provider.shutdown()
