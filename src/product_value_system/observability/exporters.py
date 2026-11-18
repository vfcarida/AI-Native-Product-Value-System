"""
Exporter configuration utilities.
"""

import os

from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import ConsoleSpanExporter


def create_console_exporter() -> ConsoleSpanExporter:
    """Create console exporter for development."""
    return ConsoleSpanExporter()


def create_otlp_exporter(endpoint: str | None = None) -> OTLPSpanExporter:
    """
    Create OTLP exporter.

    Args:
        endpoint: OTLP endpoint (defaults to env var OTEL_EXPORTER_OTLP_ENDPOINT)

    Returns:
        Configured OTLP exporter
    """
    actual_endpoint = endpoint or os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "http://localhost:4317",
    )

    return OTLPSpanExporter(
        endpoint=actual_endpoint,
        insecure=True,
    )
