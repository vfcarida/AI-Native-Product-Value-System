"""
OpenTelemetry tracing configuration and Jaeger OTLP backend integration (PROD-007).
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

from product_value_system.observability.sanitization import sanitize_attributes
from product_value_system.observability.trace_config import TracingConfig, build_sampler

logger = logging.getLogger(__name__)

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


def _auto_instrument_optional_libraries() -> list[str]:
    """Attach optional OpenTelemetry auto-instrumentors if installed."""
    instrumented: list[str] = []
    candidates = (
        ("opentelemetry.instrumentation.fastapi", "FastAPIInstrumentor", "fastapi"),
        ("opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor", "httpx"),
        ("opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor", "sqlalchemy"),
    )
    for mod_name, cls_name, label in candidates:
        try:
            mod = __import__(mod_name, fromlist=[cls_name])
            instrumentor = getattr(mod, cls_name)()
            if not getattr(instrumentor, "is_instrumented_by_opentelemetry", False):
                instrumentor.instrument()
            instrumented.append(label)
        except Exception:
            continue
    return instrumented


def init_tracing(
    service_name: str = "product-value-system",
    environment: str | None = None,
    *,
    config: TracingConfig | None = None,
    otlp_endpoint: str | None = None,
    sample_rate: float | None = None,
    span_exporter: SpanExporter | None = None,
    use_simple_processor: bool = False,
    auto_instrument: bool = True,
) -> TracerProvider:
    """Initialize OpenTelemetry tracing exporting to OpenTelemetry Collector / Jaeger.

    Preserves application availability: exporter initialization errors degrade
    gracefully instead of crashing startup, and ``OTEL_SDK_DISABLED=true`` disables
    export cleanly.
    """
    cfg = config or TracingConfig.from_env(
        service_name=service_name,
        environment=environment,
        otlp_endpoint=otlp_endpoint,
        sample_rate=sample_rate,
    )

    try:
        from importlib.metadata import version as _pkg_version

        otel_sdk_version = _pkg_version("opentelemetry-sdk")
    except Exception:
        otel_sdk_version = "unknown"

    resource = Resource.create(
        {
            SERVICE_NAME: cfg.service_name,
            SERVICE_VERSION: cfg.service_version,
            "deployment.environment": cfg.environment,
            "telemetry.sdk.name": "opentelemetry",
            "telemetry.sdk.language": "python",
            "telemetry.sdk.version": otel_sdk_version,
        }
    )

    provider = TracerProvider(resource=resource, sampler=build_sampler(cfg))

    if not cfg.disabled:
        active_exporter: SpanExporter | None = span_exporter
        if active_exporter is None:
            try:
                active_exporter = OTLPSpanExporter(
                    endpoint=cfg.otlp_endpoint,
                    insecure=cfg.otlp_insecure,
                )
            except Exception as exc:
                logger.warning("OTLP span exporter unavailable (%s); tracing degraded", exc)
                active_exporter = None

        if active_exporter is not None:
            base_processor: SpanProcessor = (
                SimpleSpanProcessor(active_exporter)
                if use_simple_processor
                else BatchSpanProcessor(
                    active_exporter,
                    max_queue_size=cfg.batch_max_queue_size,
                    max_export_batch_size=cfg.batch_max_export_batch_size,
                    schedule_delay_millis=cfg.batch_schedule_delay_millis,
                )
            )
            if cfg.redact_sensitive:
                from product_value_system.observability.sanitized_tracing import (
                    SanitizingSpanProcessor,
                )

                processor: SpanProcessor = SanitizingSpanProcessor(base_processor)
            else:
                processor = base_processor
            provider.add_span_processor(processor)

    _set_global_tracer_provider(provider)

    if auto_instrument and not cfg.disabled:
        _auto_instrument_optional_libraries()

    return provider


def get_tracer(name: str = __name__) -> trace.Tracer:
    """Return a tracer from the currently active global ``TracerProvider``."""
    return trace.get_tracer(name)


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
