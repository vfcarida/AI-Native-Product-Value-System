"""Unit tests for OpenTelemetry-correlated structured JSON logging & diagnostics (Round 12)."""

from __future__ import annotations

import io
import json
from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider

from product_value_system.observability import (
    clear_run_context,
    configure_logging,
    run_system_diagnostics,
    set_run_context,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_structured_json_logging_correlates_trace_and_redacts_secrets() -> None:
    """Structured logger injects W3C trace_id/span_id/run_id and scrubs secrets everywhere."""
    stream = io.StringIO()
    log = configure_logging(
        level="INFO",
        json_output=True,
        redact_secrets=True,
        stream=stream,
        logger_name="pvs.test.structured",
    )
    log.propagate = False

    provider = TracerProvider()
    tracer = provider.get_tracer("pvs-test")
    synthetic_key = "".join(["sk-", "9xK2mP7qR4vW8zB3nL6cT1yF5hJ0dA8wE4uQ"])
    synthetic_bearer = "Bearer " + "".join(["eyJhbGci", "OiJIUzI1NiJ9.payload.sig"])

    set_run_context("run-obs-42")
    try:
        with tracer.start_as_current_span("test-operation"):
            log.info(
                "Auth header %s failed for api_key=%s",
                synthetic_bearer,
                synthetic_key,
                extra={"credential_header": synthetic_bearer},
            )
    finally:
        clear_run_context()

    raw_line = stream.getvalue().strip()
    parsed = json.loads(raw_line)
    assert parsed["level"] == "info"
    assert parsed["run_id"] == "run-obs-42"
    assert synthetic_key not in raw_line
    assert "eyJhbGci" not in raw_line
    assert "[REDACTED]" in parsed["message"]
    assert parsed["attributes"]["credential_header"] == "[REDACTED]"


def test_run_system_diagnostics_passes_on_repository() -> None:
    """run_system_diagnostics() validates runtime, 12 YAML configs, Cedar policies, and schemas."""
    report = run_system_diagnostics(REPO_ROOT)
    assert report["healthy"] is True, f"Diagnostics failed: {report}"
    names = {c["name"] for c in report["checks"]}
    assert {
        "python_runtime",
        "yaml_config_contracts",
        "cedar_policy_engine",
        "json_schemas",
        "llm_provider_credentials",
    }.issubset(names)
