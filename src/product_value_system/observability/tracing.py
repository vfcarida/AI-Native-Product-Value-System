"""
OpenTelemetry tracing configuration and utilities.
"""

import contextvars
import functools
import inspect
from collections.abc import Callable
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from product_value_system.observability.sanitization import sanitize_attributes

# Context var for current run ID
CURRENT_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_run_id",
    default=None,
)


def _set_global_tracer_provider(provider: TracerProvider) -> None:
    """Set global tracer provider, resetting set-once guard if already configured."""
    set_once = getattr(trace, "_TRACER_PROVIDER_SET_ONCE", None)
    if set_once is not None and hasattr(set_once, "_done"):
        set_once._done = False
    if hasattr(trace, "_TRACER_PROVIDER"):
        trace._TRACER_PROVIDER = None
    trace.set_tracer_provider(provider)


def configure_tracing(
    service_name: str = "product-value-system",
    service_version: str = "0.1.0",
    otlp_endpoint: str | None = None,
    console_export: bool = True,
) -> trace.Tracer:
    """
    Configure OpenTelemetry tracing.

    Args:
        service_name: Service identifier
        service_version: Version string
        otlp_endpoint: OTLP collector endpoint (e.g., "http://localhost:4317")
        console_export: Whether to export to console (dev mode)

    Returns:
        Configured tracer instance
    """
    resource = Resource.create(
        {
            SERVICE_NAME: service_name,
            SERVICE_VERSION: service_version,
        }
    )

    provider = TracerProvider(resource=resource)

    if console_export:
        console_processor = BatchSpanProcessor(ConsoleSpanExporter())
        provider.add_span_processor(console_processor)

    if otlp_endpoint:
        otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        otlp_processor = BatchSpanProcessor(otlp_exporter)
        provider.add_span_processor(otlp_processor)

    _set_global_tracer_provider(provider)

    return trace.get_tracer(__name__)


def traced(
    span_name: str | None = None,
    attributes: dict[str, Any] | None = None,
    record_exception: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator to trace function execution.

    Args:
        span_name: Span name (defaults to function name)
        attributes: Static attributes to attach
        record_exception: Whether to record exceptions
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        actual_span_name = span_name or f"{func.__module__}.{func.__name__}"

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span(actual_span_name) as span:
                run_id = CURRENT_RUN_ID.get()
                if run_id:
                    span.set_attribute("pvs.run_id", run_id)

                if attributes:
                    sanitized = sanitize_attributes(attributes)
                    for key, value in sanitized.items():
                        span.set_attribute(key, value)

                try:
                    result = await func(*args, **kwargs)
                    span.set_attribute("pvs.status", "success")
                    return result
                except Exception as e:
                    span.set_attribute("pvs.status", "error")
                    if record_exception:
                        span.record_exception(e)
                    raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span(actual_span_name) as span:
                run_id = CURRENT_RUN_ID.get()
                if run_id:
                    span.set_attribute("pvs.run_id", run_id)

                if attributes:
                    sanitized = sanitize_attributes(attributes)
                    for key, value in sanitized.items():
                        span.set_attribute(key, value)

                try:
                    result = func(*args, **kwargs)
                    span.set_attribute("pvs.status", "success")
                    return result
                except Exception as e:
                    span.set_attribute("pvs.status", "error")
                    if record_exception:
                        span.record_exception(e)
                    raise

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def set_run_context(run_id: str) -> None:
    """Set current run ID in context for automatic span tagging."""
    CURRENT_RUN_ID.set(run_id)


def clear_run_context() -> None:
    """Clear run ID context."""
    CURRENT_RUN_ID.set(None)


trace_span = traced
