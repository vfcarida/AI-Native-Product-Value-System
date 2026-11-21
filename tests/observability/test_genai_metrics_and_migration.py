"""
Unit tests for OpenTelemetry GenAI standard metrics, legacy migration shim,
Grafana dashboard JSON, and OTEL Collector configuration (EVO-021).
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import pytest
import yaml
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from product_value_system.observability.genai_conventions import GenAIAttributes
from product_value_system.observability.genai_metrics import GenAIMetrics
from product_value_system.observability.genai_tracer import GenAITracer
from product_value_system.observability.migration_shim import (
    ATTRIBUTE_MIGRATION_MAP,
    CompatibilityTracer,
    migrate_attributes,
)
from product_value_system.security.secret_detection import SecretDetector

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _collect_metrics_by_name(reader: InMemoryMetricReader) -> dict[str, Any]:
    """Flatten InMemoryMetricReader output into a dict keyed by OTEL metric name."""
    data = reader.get_metrics_data()
    found: dict[str, Any] = {}
    if data is None:
        return found
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                found[metric.name] = metric
    return found


@pytest.mark.unit
def test_genai_metrics_records_all_4_standard_instruments() -> None:
    """GenAIMetrics exposes and records all 4 standard OTEL GenAI client metrics."""
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    metrics = GenAIMetrics(meter=meter_provider.get_meter("test.genai.metrics"))

    metrics.record_token_usage("anthropic", "claude-sonnet-4", "prompt", 500)
    metrics.record_token_usage("anthropic", "claude-sonnet-4", "completion", 250)
    metrics.record_operation_duration("anthropic", "chat", 1.25, finish_reason="stop")
    metrics.record_error("anthropic", "RateLimitError")
    metrics.record_cost("anthropic", "claude-sonnet-4", 0.00525)

    recorded = _collect_metrics_by_name(reader)
    assert set(recorded.keys()) == {
        "gen_ai.client.token.usage",
        "gen_ai.client.operation.duration",
        "gen_ai.client.operation.error",
        "gen_ai.client.operation.cost",
    }

    assert recorded["gen_ai.client.token.usage"].unit == "tokens"
    assert recorded["gen_ai.client.operation.duration"].unit == "seconds"
    assert recorded["gen_ai.client.operation.error"].unit == "errors"
    assert recorded["gen_ai.client.operation.cost"].unit == "USD"

    snapshot = metrics.get_snapshot()
    assert snapshot["prompt_tokens_total"] == 500
    assert snapshot["completion_tokens_total"] == 250
    assert snapshot["total_tokens"] == 750
    assert snapshot["operations_total"] == 1
    assert snapshot["errors_total"] == 1
    assert snapshot["cost_usd_total"] == pytest.approx(0.00525)
    assert snapshot["mean_duration_seconds"] == pytest.approx(1.25)

    meter_provider.shutdown()


@pytest.mark.unit
def test_genai_tracer_auto_records_metrics_on_chat_completion() -> None:
    """GenAITracer automatically updates token, cost, and duration metrics on completion."""
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    metrics = GenAIMetrics(meter=meter_provider.get_meter("test.auto"))

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    genai_tracer = GenAITracer(tracer_provider.get_tracer("test.auto"), metrics=metrics)

    with genai_tracer.trace_chat_completion(
        operation_name="chat.auto_metrics",
        model="gpt-4o",
        messages=[{"role": "user", "content": "Draft ADR"}],
    ) as span:
        span.record_completion(
            response_id="chatcmpl-auto1",
            completion="ADR-002 drafted.",
            prompt_tokens=300,
            completion_tokens=150,
            cost_usd=0.004,
        )

    snap = metrics.get_snapshot()
    assert snap["prompt_tokens_total"] == 300
    assert snap["completion_tokens_total"] == 150
    assert snap["total_tokens"] == 450
    assert snap["operations_total"] == 1
    assert snap["errors_total"] == 0
    assert snap["cost_usd_total"] == pytest.approx(0.004)
    assert snap["mean_duration_seconds"] >= 0.0

    tracer_provider.shutdown()
    meter_provider.shutdown()


@pytest.mark.unit
def test_genai_tracer_auto_records_metrics_on_error() -> None:
    """Failed spans increment gen_ai.client.operation.error and record error finish_reason."""
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    metrics = GenAIMetrics(meter=meter_provider.get_meter("test.err"))

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    genai_tracer = GenAITracer(tracer_provider.get_tracer("test.err"), metrics=metrics)

    with (
        pytest.raises(TimeoutError, match="Upstream timeout"),
        genai_tracer.trace_chat_completion(
            operation_name="chat.timeout",
            model="claude-sonnet-4",
            messages=[{"role": "user", "content": "Hello"}],
        ),
    ):
        raise TimeoutError("Upstream timeout")

    snap = metrics.get_snapshot()
    assert snap["errors_total"] == 1
    assert snap["operations_total"] == 1

    recorded = _collect_metrics_by_name(reader)
    error_points = recorded["gen_ai.client.operation.error"].data.data_points
    assert len(error_points) == 1
    assert error_points[0].attributes["gen_ai.system"] == "anthropic"
    assert error_points[0].attributes["error.type"] == "TimeoutError"

    duration_points = recorded["gen_ai.client.operation.duration"].data.data_points
    assert duration_points[0].attributes["gen_ai.response.finish_reason"] == "error"

    tracer_provider.shutdown()
    meter_provider.shutdown()


@pytest.mark.unit
def test_genai_tracer_text_embedding_and_rerank_record_metrics() -> None:
    """Text completion, embedding, and rerank spans also populate GenAIMetrics."""
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    metrics = GenAIMetrics(meter=meter_provider.get_meter("test.multi"))

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    genai_tracer = GenAITracer(tracer_provider.get_tracer("test.multi"), metrics=metrics)

    with genai_tracer.trace_text_completion(
        operation_name="completion.op",
        model="davinci-002",
        prompt="Hi",
    ) as t_span:
        t_span.record_completion(
            "c1", "Hello", prompt_tokens=10, completion_tokens=5, cost_usd=0.01
        )

    with genai_tracer.trace_embedding(
        operation_name="embed.op",
        model="text-embedding-3-small",
        input_text="Sample text",
    ) as e_span:
        e_span.record_embedding(dimensions=1536, token_count=20, cost_usd=0.002)

    with genai_tracer.trace_rerank(
        operation_name="rerank.op",
        model="rerank-english-v3.0",
        query="telemetry",
        documents=["a", "b"],
        top_n=1,
    ) as r_span:
        r_span.record_rerank(returned_count=1, token_count=30, cost_usd=0.003)

    snap = metrics.get_snapshot()
    assert snap["operations_total"] == 3
    assert snap["prompt_tokens_total"] == 60
    assert snap["completion_tokens_total"] == 5
    assert snap["cost_usd_total"] == pytest.approx(0.015)

    tracer_provider.shutdown()
    meter_provider.shutdown()


@pytest.mark.unit
def test_migrate_attributes_converts_all_legacy_llm_keys_with_deprecation_warnings() -> None:
    """Every deprecated llm.* attribute converts cleanly and emits DeprecationWarning."""
    legacy_payload = {old_key: f"val-{idx}" for idx, old_key in enumerate(ATTRIBUTE_MIGRATION_MAP)}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        migrated = migrate_attributes(legacy_payload)

    deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecation_warnings) == len(ATTRIBUTE_MIGRATION_MAP)

    for old_key, new_key in ATTRIBUTE_MIGRATION_MAP.items():
        assert old_key not in migrated
        assert migrated[new_key] == legacy_payload[old_key]


@pytest.mark.unit
def test_migrate_attributes_preserves_unknown_keys_and_prefers_new_keys() -> None:
    """Unknown keys pass through untouched; standard gen_ai.* values override legacy aliases."""
    mixed = {
        "llm.model": "old-model-name",
        "gen_ai.request.model": "claude-sonnet-4",
        "pvs.agent.name": "discovery-agent",
        "llm.temperature": 0.5,
    }
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        migrated = migrate_attributes(mixed)

    assert migrated["gen_ai.request.model"] == "claude-sonnet-4"
    assert migrated["gen_ai.request.temperature"] == 0.5
    assert migrated["pvs.agent.name"] == "discovery-agent"
    assert "llm.model" not in migrated


@pytest.mark.unit
def test_migrate_attributes_silent_mode() -> None:
    """emit_warnings=False suppresses DeprecationWarning while still translating keys."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        migrated = migrate_attributes(
            {"llm.model": "gpt-4o", "llm.tokens": 100},
            emit_warnings=False,
        )

    assert caught == []
    assert migrated == {
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.usage.total_tokens": 100,
    }


@pytest.mark.unit
def test_compatibility_tracer_accepts_legacy_llm_kwargs() -> None:
    """CompatibilityTracer translates legacy llm.* kwargs into standard gen_ai.* spans."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    compat = CompatibilityTracer(GenAITracer(provider.get_tracer("test.compat")))

    with (
        pytest.warns(DeprecationWarning, match="deprecated"),
        compat.trace_llm_call(
            operation_name="legacy.call",
            messages=[{"role": "user", "content": "Legacy caller"}],
            **{
                "llm.model": "claude-sonnet-4",
                "llm.temperature": 0.25,
                "llm.max_tokens": 256,
                "llm.provider": "anthropic",
            },
        ) as span,
    ):
        span.record_completion(
            response_id="msg_compat",
            completion="Works seamlessly",
            prompt_tokens=15,
            completion_tokens=5,
        )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert spans[0].name == "legacy.call"
    assert attrs[GenAIAttributes.SYSTEM] == "anthropic"
    assert attrs[GenAIAttributes.REQUEST_MODEL] == "claude-sonnet-4"
    assert attrs[GenAIAttributes.REQUEST_TEMPERATURE] == 0.25
    assert attrs[GenAIAttributes.REQUEST_MAX_TOKENS] == 256
    assert attrs[GenAIAttributes.USAGE_TOTAL_TOKENS] == 20
    provider.shutdown()


@pytest.mark.unit
def test_compatibility_tracer_accepts_standard_genai_kwargs_without_warnings() -> None:
    """CompatibilityTracer emits zero warnings when called with standard gen_ai.* keys."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    compat = CompatibilityTracer(GenAITracer(provider.get_tracer("test.compat.std")))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with compat.trace_llm_call(
            operation_name="std.call",
            messages=[{"role": "user", "content": "Standard caller"}],
            **{
                "gen_ai.request.model": "gpt-4o",
                "gen_ai.request.temperature": 0.1,
            },
        ) as span:
            span.record_completion("id-std", "OK", prompt_tokens=5, completion_tokens=2)

    assert [w for w in caught if issubclass(w.category, DeprecationWarning)] == []
    spans = exporter.get_finished_spans()
    assert dict(spans[0].attributes or {})[GenAIAttributes.REQUEST_MODEL] == "gpt-4o"
    provider.shutdown()


@pytest.mark.unit
def test_grafana_genai_dashboard_and_otel_collector_config_valid_and_zero_secrets() -> None:
    """Monitoring artifacts parse cleanly, reference gen_ai_* metrics, and contain zero secrets."""
    detector = SecretDetector()

    dashboard_path = REPO_ROOT / "monitoring" / "grafana" / "dashboards" / "genai_overview.json"
    collector_path = REPO_ROOT / "monitoring" / "otel-collector-config.yml"

    assert dashboard_path.exists()
    assert collector_path.exists()

    dashboard_text = dashboard_path.read_text(encoding="utf-8")
    collector_text = collector_path.read_text(encoding="utf-8")

    assert detector.find_secrets(dashboard_text) == []
    assert detector.find_secrets(collector_text) == []

    dashboard = json.loads(dashboard_text)
    assert dashboard["uid"] == "pvs-genai-overview"
    assert len(dashboard["panels"]) == 4
    panel_exprs = " ".join(
        target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", [])
    )
    assert "gen_ai_client_token_usage" in panel_exprs
    assert "gen_ai_client_operation_duration" in panel_exprs
    assert "gen_ai_client_operation_error" in panel_exprs
    assert "gen_ai_client_operation_cost" in panel_exprs

    collector = yaml.safe_load(collector_text)
    assert "otlp" in collector["receivers"]
    assert collector["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"] == "0.0.0.0:4317"
    assert collector["receivers"]["otlp"]["protocols"]["http"]["endpoint"] == "0.0.0.0:4318"
    assert "traces" in collector["service"]["pipelines"]
    assert "metrics" in collector["service"]["pipelines"]
