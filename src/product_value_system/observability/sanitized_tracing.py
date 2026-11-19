"""
Observability with secret sanitization.
"""

from types import MappingProxyType
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from product_value_system.observability.tracing import _set_global_tracer_provider
from product_value_system.security.output_sanitization import OutputSanitizer


class SanitizingSpanProcessor(SpanProcessor):
    """
    Span processor that sanitizes attributes before export.
    """

    def __init__(self, next_processor: SpanProcessor | None = None) -> None:
        self.sanitizer = OutputSanitizer()
        self.next_processor = next_processor

    def on_start(
        self,
        span: Span,
        parent_context: Any | None = None,
    ) -> None:
        """Called when span starts."""
        if self.next_processor:
            self.next_processor.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        """
        Called when span ends. Sanitize attributes before export.
        """
        if span.attributes:
            sanitized: dict[str, Any] = {}
            for key, value in span.attributes.items():
                if isinstance(value, str):
                    sanitized[key] = self.sanitizer.sanitize_for_logging(value)
                else:
                    sanitized[key] = value
            if hasattr(span, "_attributes"):
                span._attributes = MappingProxyType(sanitized)

        if self.next_processor:
            self.next_processor.on_end(span)

    def shutdown(self) -> None:
        """Shutdown processor."""
        if self.next_processor:
            self.next_processor.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Force flush."""
        if self.next_processor:
            return self.next_processor.force_flush(timeout_millis)
        return True


def setup_sanitized_tracing(service_name: str) -> None:
    """
    Set up OpenTelemetry tracing with secret sanitization.

    Args:
        service_name: Service name for traces
    """
    provider = TracerProvider()

    console_exporter = ConsoleSpanExporter()
    batch_processor = BatchSpanProcessor(console_exporter)
    sanitizing_processor = SanitizingSpanProcessor(batch_processor)

    provider.add_span_processor(sanitizing_processor)
    _set_global_tracer_provider(provider)
