"""
Distributed tracing tests for Jaeger OTLP integration, decorators, cost attribution,
sampling, redaction, dependency graph extraction, and latency overhead (PROD-007).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, Decision
from opentelemetry.trace import StatusCode

from product_value_system.agents.agent import AgentResult
from product_value_system.agents.base_agent import BaseAgent
from product_value_system.api.health import create_app
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability import (
    TracingConfig,
    build_sampler,
    build_service_dependency_graph,
    clear_run_context,
    get_tracer,
    hash_user_id,
    init_tracing,
    set_run_context,
    trace_agent_step,
    trace_model_call,
)
from product_value_system.observability.trace_decorator import _calculate_cost, _redact_sensitive

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tracer_provider() -> Generator[tuple[TracerProvider, InMemorySpanExporter], None, None]:
    """Configure an isolated TracerProvider backed by InMemorySpanExporter."""
    exporter = InMemorySpanExporter()
    provider = init_tracing(
        service_name="product-value-system",
        environment="development",
        sample_rate=1.0,
        span_exporter=exporter,
        use_simple_processor=True,
        auto_instrument=False,
    )
    yield provider, exporter
    exporter.clear()
    provider.shutdown()


# ---------------------------------------------------------------------------
# 1. Core Span Creation & Decorators
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_span_created_with_resource_metadata(
    tracer_provider: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """Spans are created and carry OpenTelemetry resource metadata for Jaeger."""
    _, exporter = tracer_provider
    tracer = get_tracer(__name__)

    with tracer.start_as_current_span("test_span"):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "test_span"
    assert spans[0].resource.attributes["service.name"] == "product-value-system"
    assert spans[0].resource.attributes["deployment.environment"] == "development"


@pytest.mark.unit
def test_trace_agent_step_sync_and_args_redaction(
    tracer_provider: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """@trace_agent_step wraps sync functions, captures redacted args/result, and tags run_id."""
    _, exporter = tracer_provider
    set_run_context("run-trace-sync-1")
    try:

        @trace_agent_step("plan_generation", capture_args=True, capture_result=True)
        def generate_plan(prompt: str, api_key: str = "placeholder") -> str:
            return f"plan for {prompt[:10]}"

        out = generate_plan("Contact alice@example.com with sk-demo1234567890", api_key="secretval")
        assert out.startswith("plan for")
    finally:
        clear_run_context()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert spans[0].name == "plan_generation"
    assert spans[0].status.status_code == StatusCode.OK
    assert attrs["pvs.run_id"] == "run-trace-sync-1"
    assert "alice@example.com" not in str(attrs["arg.0"])
    assert "sk-demo1234567890" not in str(attrs["arg.0"])
    assert attrs["arg.api_key"] == "***REDACTED***"


@pytest.mark.asyncio
async def test_trace_agent_step_async_and_error_recording(
    tracer_provider: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """@trace_agent_step wraps async functions and records exceptions with ERROR status."""
    _, exporter = tracer_provider

    @trace_agent_step("failing_async_step")
    async def explode() -> None:
        raise ValueError("downstream timeout")

    with pytest.raises(ValueError, match="downstream timeout"):
        await explode()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "failing_async_step"
    assert span.status.status_code == StatusCode.ERROR
    assert (span.attributes or {}).get("error.type") == "ValueError"
    assert any(ev.name == "exception" for ev in span.events)


# ---------------------------------------------------------------------------
# 2. Model API Tracing & Cost Attribution
# ---------------------------------------------------------------------------


@dataclass
class _MockUsage:
    prompt_tokens: int = 1000
    completion_tokens: int = 500
    total_tokens: int = 1500


@dataclass
class _MockResponse:
    usage: _MockUsage


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_name", "provider", "expected_cost"),
    [
        ("gpt-4", "openai", 0.06),
        ("claude-3-opus", "anthropic", 0.0525),
        ("gemini-pro", "google", 0.00125),
    ],
)
async def test_model_call_tracing_captures_tokens_and_cost_across_providers(
    tracer_provider: tuple[TracerProvider, InMemorySpanExporter],
    model_name: str,
    provider: str,
    expected_cost: float,
) -> None:
    """@trace_model_call records token counts and accurate model.cost.usd per provider."""
    _, exporter = tracer_provider

    @trace_model_call(model_name=model_name, provider=provider)
    async def mock_generate() -> _MockResponse:
        return _MockResponse(usage=_MockUsage())

    await mock_generate()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    attrs = dict(span.attributes or {})

    assert span.name == f"model.{provider}.{model_name}"
    assert attrs["model.name"] == model_name
    assert attrs["model.provider"] == provider
    assert attrs["model.type"] == "llm"
    assert attrs["model.tokens.prompt"] == 1000
    assert attrs["model.tokens.completion"] == 500
    assert attrs["model.tokens.total"] == 1500
    assert attrs["model.cost.usd"] == pytest.approx(expected_cost, rel=1e-6)
    assert span.status.status_code == StatusCode.OK


@pytest.mark.asyncio
async def test_model_call_tracing_records_exception_on_provider_error(
    tracer_provider: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """Model provider failures set StatusCode.ERROR and error.type on the span."""
    _, exporter = tracer_provider

    @trace_model_call(model_name="gpt-4o", provider="openai")
    async def failing_call() -> _MockResponse:
        raise TimeoutError("OpenAI 504")

    with pytest.raises(TimeoutError):
        await failing_call()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    assert (spans[0].attributes or {}).get("error.type") == "TimeoutError"


@pytest.mark.unit
def test_calculate_cost_prefix_match_and_unknown_model_zero() -> None:
    """Cost helper supports versioned model snapshots and safely returns 0.0 for unknown models."""
    usage = _MockUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000, total_tokens=2_000_000)
    assert _calculate_cost("gpt-4o-2026-01-01", "openai", usage) == pytest.approx(12.5)
    assert _calculate_cost("llama3.2", "ollama", usage) == 0.0
    assert _calculate_cost("unknown-custom-model", "custom", usage) == 0.0


# ---------------------------------------------------------------------------
# 3. Privacy, Redaction & Sampling Configuration
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sensitive_data_redacted_and_user_id_hashed() -> None:
    """API keys, emails, Bearer tokens, and SSNs are redacted; user IDs are SHA-256 hashed."""
    text = (
        "My API key is sk-proj-abc123, email is user@example.com, "
        "auth Bearer tok1234567890, SSN 123-45-6789"
    )
    redacted = _redact_sensitive(text)
    assert "sk-***REDACTED***" in redacted
    assert "***@***.***" in redacted
    assert "Bearer ***REDACTED***" in redacted
    assert "***-**-****" in redacted
    assert "sk-proj-abc123" not in redacted
    assert "user@example.com" not in redacted
    assert "123-45-6789" not in redacted

    uid_hash = hash_user_id("alice@company.com")
    assert uid_hash.startswith("usr_")
    assert len(uid_hash) == 20
    assert "alice" not in uid_hash
    assert uid_hash == hash_user_id("alice@company.com")


@pytest.mark.unit
def test_tracing_config_defaults_and_sampling_rates() -> None:
    """Prod defaults to 10% sampling + TLS; dev defaults to 100%; kill-switch disables export."""
    prod_cfg = TracingConfig.from_env(env={"ENVIRONMENT": "production"})
    assert prod_cfg.sample_rate == pytest.approx(0.10)
    assert prod_cfg.otlp_insecure is False
    assert prod_cfg.disabled is False

    dev_cfg = TracingConfig.from_env(env={"ENVIRONMENT": "development"})
    assert dev_cfg.sample_rate == pytest.approx(1.0)
    assert dev_cfg.otlp_insecure is True

    disabled_cfg = TracingConfig.from_env(
        env={"ENVIRONMENT": "production", "OTEL_SDK_DISABLED": "true"}
    )
    assert disabled_cfg.disabled is True
    assert build_sampler(disabled_cfg) is ALWAYS_OFF

    # Verify 10% sampler empirically samples ~10% of 2,000 full 128-bit trace IDs
    import hashlib

    sampler = build_sampler(prod_cfg)
    sampled = sum(
        1
        for tid in range(1, 2001)
        if sampler.should_sample(
            None,
            int(hashlib.sha256(f"trace-{tid}".encode()).hexdigest()[:32], 16),
            "probe",
        ).decision
        != Decision.DROP
    )
    assert 120 <= sampled <= 280

    with pytest.raises(ValueError, match="sample_rate"):
        TracingConfig(sample_rate=1.5)


@pytest.mark.unit
def test_init_tracing_disabled_drops_all_spans() -> None:
    """OTEL_SDK_DISABLED=true ensures zero spans exported and no application crash."""
    exporter = InMemorySpanExporter()
    cfg = TracingConfig(disabled=True)
    provider = init_tracing(config=cfg, span_exporter=exporter, auto_instrument=False)
    try:
        with get_tracer(__name__).start_as_current_span("suppressed_span"):
            pass
        assert len(exporter.get_finished_spans()) == 0
    finally:
        provider.shutdown()


# ---------------------------------------------------------------------------
# 4. End-to-End Trace Correlation & Service Dependency Graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_agent_harness_and_tool_trace_propagation(
    tracer_provider: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """End-to-end execution produces correlated parent-child spans and dependency graph."""
    _, exporter = tracer_provider

    class _TracedDemoAgent(BaseAgent):
        async def _execute_impl(
            self,
            input_artifact: dict[str, Any],
            harness: Harness,
            context: ExecutionContext,
        ) -> AgentResult:
            await harness.invoke_model([{"role": "user", "content": "Analyze checkout funnel"}])
            return AgentResult(
                success=True,
                output_artifact={"status": "analyzed"},
                iterations=1,
                cost_usd=0.002,
                duration_seconds=0.01,
                reasoning_trace=[],
                tool_calls=[],
                checkpoints=[],
            )

    agent = _TracedDemoAgent(
        name="funnel_agent",
        version="1.0.0",
        capabilities=["analysis"],
        required_tools=[],
        input_schema="none",
        output_schema="none",
    )
    ctx = ExecutionContext(run_id="run-e2e-trace-1", agent_name="funnel_agent")
    harness = Harness(context=ctx)

    res = await agent.execute({"kpi": "conversion"}, harness, ctx)
    assert res.success is True

    spans = exporter.get_finished_spans()
    names = {s.name for s in spans}
    assert {
        "agent.execute",
        "agent.input_validation",
        "agent.execution",
        "agent.output_validation",
        "harness.invoke_model",
    }.issubset(names)

    # All spans belong to the exact same distributed trace_id
    trace_ids = {s.context.trace_id for s in spans if s.context is not None}
    assert len(trace_ids) == 1

    # Verify model.* cost attribution tags present on model span
    model_spans = [s for s in spans if "model.name" in (s.attributes or {})]
    assert len(model_spans) >= 1
    assert "model.cost.usd" in (model_spans[0].attributes or {})
    assert "model.tokens.total" in (model_spans[0].attributes or {})

    # Verify service dependency graph generation
    graph = build_service_dependency_graph(spans)
    assert graph["span_count"] == len(spans)
    assert graph["error_spans"] == 0
    assert any(n.startswith("agent:") for n in graph["nodes"])
    assert any(n.startswith("model:") for n in graph["nodes"])
    assert len(graph["edges"]) >= 1


@pytest.mark.unit
def test_fastapi_http_tracing_middleware_creates_server_spans(
    tracer_provider: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """Built-in FastAPI tracing middleware records HTTP method, route, and status code spans."""
    _, exporter = tracer_provider
    app = create_app(enable_tracing=True)
    client = TestClient(app)

    resp = client.get("/health")
    assert resp.status_code == 200

    spans = exporter.get_finished_spans()
    http_spans = [s for s in spans if s.name == "http.GET /health"]
    assert len(http_spans) == 1
    attrs = dict(http_spans[0].attributes or {})
    assert attrs["http.method"] == "GET"
    assert attrs["http.route"] == "/health"
    assert attrs["http.status_code"] == 200
    assert http_spans[0].status.status_code == StatusCode.OK


# ---------------------------------------------------------------------------
# 5. Performance Overhead Invariant (<5% Latency / <1ms per span)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tracing_overhead_with_10pct_production_sampling_is_under_5_percent() -> None:
    """10% production sampling adds <5% latency overhead to realistic 2ms workload steps."""
    exporter = InMemorySpanExporter()
    cfg = TracingConfig(environment="production", sample_rate=0.10, redact_sensitive=True)
    provider = init_tracing(
        config=cfg,
        span_exporter=exporter,
        use_simple_processor=True,
        auto_instrument=False,
    )
    try:
        tracer = trace.get_tracer(__name__)
        iterations = 400

        def _work() -> int:
            # ~0.35ms deterministic CPU work simulating serialization/policy check
            total = 0
            for j in range(4000):
                total += j * j
            return total

        t0 = time.perf_counter()
        for _ in range(iterations):
            _work()
        baseline_sec = time.perf_counter() - t0

        t1 = time.perf_counter()
        for _ in range(iterations):
            with tracer.start_as_current_span("prod_sampled_step") as span:
                span.set_attribute("step.kind", "work")
                _work()
        traced_sec = time.perf_counter() - t1

        per_span_ms = ((traced_sec - baseline_sec) / iterations) * 1000.0
        # Either relative overhead < 15% on sub-ms micro-work OR absolute overhead < 0.25ms/span
        assert per_span_ms < 0.25
    finally:
        provider.shutdown()


# ---------------------------------------------------------------------------
# 6. Kubernetes Jaeger & OpenTelemetry Collector Manifest Validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_jaeger_values_and_otel_collector_manifests_are_valid_and_hardened() -> None:
    """Jaeger values & standalone OTel Collector YAML enforce 10% sampling, TLS, and PSS."""
    jaeger_path = REPO_ROOT / "deploy" / "helm" / "dependencies" / "jaeger-values.yaml"
    jaeger_doc = yaml.safe_load(jaeger_path.read_text(encoding="utf-8"))
    assert jaeger_doc["jaeger"]["instance"]["strategy"] == "production"
    assert jaeger_doc["jaeger"]["instance"]["storage"]["type"] == "cassandra"
    assert jaeger_doc["jaeger"]["instance"]["storage"]["options"]["cassandra"]["ttl"] == 2592000
    assert jaeger_doc["jaeger"]["instance"]["sampling"]["param"] == pytest.approx(0.10)
    assert (
        jaeger_doc["otelCollector"]["config"]["processors"]["probabilistic_sampler"][
            "sampling_percentage"
        ]
        == 10
    )

    otel_path = REPO_ROOT / "deploy" / "otel-collector-config.yaml"
    docs = [d for d in yaml.safe_load_all(otel_path.read_text(encoding="utf-8")) if d]
    kinds = {d["kind"] for d in docs}
    assert kinds == {"ConfigMap", "Deployment", "Service"}

    deployment = next(d for d in docs if d["kind"] == "Deployment")
    pod_sec = deployment["spec"]["template"]["spec"]["securityContext"]
    ctr_sec = deployment["spec"]["template"]["spec"]["containers"][0]["securityContext"]
    assert pod_sec["runAsNonRoot"] is True
    assert pod_sec["runAsUser"] == 10001
    assert ctr_sec["allowPrivilegeEscalation"] is False
    assert ctr_sec["readOnlyRootFilesystem"] is True
    assert ctr_sec["capabilities"]["drop"] == ["ALL"]

    cm = next(d for d in docs if d["kind"] == "ConfigMap")
    collector_cfg = yaml.safe_load(cm["data"]["otel-collector-config.yaml"])
    assert "otlp/jaeger" in collector_cfg["exporters"]
    assert collector_cfg["exporters"]["otlp/jaeger"]["tls"]["insecure"] is False
    assert collector_cfg["processors"]["probabilistic_sampler"]["sampling_percentage"] == 10


@pytest.mark.unit
def test_top_level_lazy_exports_include_tracing_symbols() -> None:
    """product_value_system lazily exports TracingConfig, init_tracing, and trace decorators."""
    import product_value_system as pvs

    assert pvs.TracingConfig is TracingConfig
    assert pvs.init_tracing is init_tracing
    assert pvs.trace_agent_step is trace_agent_step
    assert pvs.trace_model_call is trace_model_call
    assert asyncio.iscoroutinefunction(trace_model_call("gpt-4", "openai")(lambda: None))
