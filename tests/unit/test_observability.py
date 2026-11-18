"""
Unit tests for observability components (P006).
"""

from collections.abc import Generator
from decimal import Decimal
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from product_value_system.observability.cost_tracker import CostTracker
from product_value_system.observability.genai_conventions import (
    GenAIAttributes,
    instrument_model_call,
)
from product_value_system.observability.sanitization import (
    sanitize_attributes,
    sanitize_string,
)
from product_value_system.observability.tracing import (
    _set_global_tracer_provider,
    clear_run_context,
    set_run_context,
    traced,
)


@pytest.fixture
def in_memory_tracer() -> Generator[tuple[trace.Tracer, InMemorySpanExporter], None, None]:
    """Create tracer with in-memory exporter for testing."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _set_global_tracer_provider(provider)

    tracer = trace.get_tracer(__name__)

    yield tracer, exporter

    exporter.clear()


@pytest.mark.unit
def test_traced_decorator_sync(
    in_memory_tracer: tuple[trace.Tracer, InMemorySpanExporter],
) -> None:
    """Test traced decorator on sync function."""
    _, exporter = in_memory_tracer

    @traced("test.function")
    def my_function(x: int) -> int:
        return x * 2

    result = my_function(5)

    assert result == 10

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "test.function"
    assert spans[0].attributes is not None
    assert spans[0].attributes["pvs.status"] == "success"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_traced_decorator_async(
    in_memory_tracer: tuple[trace.Tracer, InMemorySpanExporter],
) -> None:
    """Test traced decorator on async function."""
    _, exporter = in_memory_tracer

    @traced("test.async_function")
    async def my_async_function(x: int) -> int:
        return x * 3

    result = await my_async_function(7)

    assert result == 21

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "test.async_function"


@pytest.mark.unit
def test_traced_decorator_with_exception(
    in_memory_tracer: tuple[trace.Tracer, InMemorySpanExporter],
) -> None:
    """Test traced decorator records exceptions."""
    _, exporter = in_memory_tracer

    @traced("test.error_function")
    def error_function() -> None:
        raise ValueError("Test error")

    with pytest.raises(ValueError, match="Test error"):
        error_function()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes is not None
    assert spans[0].attributes["pvs.status"] == "error"
    assert len(spans[0].events) > 0


@pytest.mark.unit
def test_run_context(
    in_memory_tracer: tuple[trace.Tracer, InMemorySpanExporter],
) -> None:
    """Test run ID context propagation."""
    _, exporter = in_memory_tracer

    set_run_context("run-12345")

    @traced("test.with_context")
    def my_function() -> None:
        pass

    my_function()

    spans = exporter.get_finished_spans()
    assert spans[0].attributes is not None
    assert spans[0].attributes["pvs.run_id"] == "run-12345"

    clear_run_context()


@pytest.mark.unit
def test_genai_attributes() -> None:
    """Test GenAI semantic convention attributes."""
    provider = TracerProvider()
    tracer = provider.get_tracer(__name__)

    with tracer.start_as_current_span("test") as span:
        instrument_model_call(
            span=span,
            system="openai",
            model="gpt-4",
            operation="chat",
            input_tokens=1500,
            output_tokens=500,
            finish_reason="stop",
            cost_usd=0.125,
        )

        span_any: Any = span
        assert span_any.attributes[GenAIAttributes.SYSTEM] == "openai"
        assert span_any.attributes[GenAIAttributes.REQUEST_MODEL] == "gpt-4"
        assert span_any.attributes[GenAIAttributes.USAGE_INPUT_TOKENS] == 1500
        assert span_any.attributes[GenAIAttributes.USAGE_OUTPUT_TOKENS] == 500
        assert span_any.attributes[GenAIAttributes.COST_USD] == 0.125


@pytest.mark.unit
def test_cost_tracker_compute() -> None:
    """Test cost computation."""
    tracker = CostTracker()

    breakdown = tracker.compute_cost(
        model="gpt-4",
        input_tokens=1000,
        output_tokens=500,
    )

    assert breakdown.input_cost_usd == Decimal("0.03")
    assert breakdown.output_cost_usd == Decimal("0.03")
    assert breakdown.total_cost_usd == Decimal("0.06")


@pytest.mark.unit
def test_cost_tracker_summary() -> None:
    """Test cost tracker summary."""
    tracker = CostTracker()

    tracker.compute_cost("gpt-4", 1000, 500)
    tracker.compute_cost("gpt-4", 2000, 1000)
    tracker.compute_cost("gpt-3.5-turbo", 5000, 2000)

    summary = tracker.get_summary()

    assert summary["total_input_tokens"] == 8000
    assert summary["total_output_tokens"] == 3500
    assert summary["calls_by_model"]["gpt-4"] == 2
    assert summary["calls_by_model"]["gpt-3.5-turbo"] == 1


@pytest.mark.unit
def test_sanitize_string() -> None:
    """Test PII sanitization."""
    text = "Contact john.doe@example.com or call 555-123-4567"

    sanitized = sanitize_string(text)

    assert "[EMAIL_REDACTED]" in sanitized
    assert "[PHONE_REDACTED]" in sanitized
    assert "john.doe@example.com" not in sanitized
    assert "555-123-4567" not in sanitized


@pytest.mark.unit
def test_sanitize_attributes() -> None:
    """Test attribute sanitization."""
    attrs = {
        "user_name": "Alice",
        "email": "alice@example.com",
        "api_key": "sk-1234567890",
        "model": "gpt-4",
        "input_tokens": 1500,
    }

    sanitized = sanitize_attributes(attrs)

    assert "user_name" in sanitized
    assert "model" in sanitized
    assert "input_tokens" in sanitized
    assert "api_key" not in sanitized
    assert "[EMAIL_REDACTED]" in sanitized["email"]


@pytest.mark.unit
def test_cost_tracker_unknown_model() -> None:
    """Test cost tracker with unknown model."""
    tracker = CostTracker()

    with pytest.raises(ValueError, match="not in pricing table"):
        tracker.compute_cost("unknown-model", 1000, 500)


@pytest.mark.unit
def test_cost_tracker_custom_pricing() -> None:
    """Test cost tracker with custom pricing."""
    custom = {"local-llama": {"input": Decimal("0.0"), "output": Decimal("0.0")}}

    tracker = CostTracker(custom_pricing=custom)

    breakdown = tracker.compute_cost("local-llama", 1000, 500)

    assert breakdown.total_cost_usd == Decimal("0.0")
