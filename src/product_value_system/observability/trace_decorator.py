"""
Tracing decorators, cost attribution helpers, and service dependency graph extraction (PROD-007).

Provides:
- ``@trace_agent_step``: Sync/async span decorator with sensitive-data redaction and error capture.
- ``@trace_model_call``: Model API span decorator capturing token counts and ``model.cost.usd``.
- ``_redact_sensitive``: Masks API keys, Bearer tokens, emails, SSNs, and card numbers.
- ``build_service_dependency_graph``: Extracts service/component call graphs and latency
  waterfalls directly from finished OpenTelemetry spans.
"""

from __future__ import annotations

import asyncio
import functools
import re
from collections.abc import Callable, Sequence
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import Status, StatusCode

from product_value_system.observability.genai_conventions import GenAIAttributes
from product_value_system.observability.tracing import CURRENT_RUN_ID

# Pricing per 1M tokens (USD) for Jaeger cost attribution spans
_MODEL_PRICING_PER_1M: dict[tuple[str, str], dict[str, float]] = {
    # OpenAI
    ("gpt-4", "openai"): {"prompt": 30.0, "completion": 60.0},
    ("gpt-4-turbo", "openai"): {"prompt": 10.0, "completion": 30.0},
    ("gpt-4o", "openai"): {"prompt": 2.5, "completion": 10.0},
    ("gpt-4o-mini", "openai"): {"prompt": 0.15, "completion": 0.60},
    ("gpt-3.5-turbo", "openai"): {"prompt": 0.5, "completion": 1.5},
    # Azure OpenAI
    ("gpt-4o", "azure_openai"): {"prompt": 2.5, "completion": 10.0},
    ("gpt-4o-mini", "azure_openai"): {"prompt": 0.15, "completion": 0.60},
    # Anthropic
    ("claude-3-opus", "anthropic"): {"prompt": 15.0, "completion": 75.0},
    ("claude-3-opus-20240229", "anthropic"): {"prompt": 15.0, "completion": 75.0},
    ("claude-3-sonnet", "anthropic"): {"prompt": 3.0, "completion": 15.0},
    ("claude-3-5-sonnet", "anthropic"): {"prompt": 3.0, "completion": 15.0},
    ("claude-3-5-sonnet-20241022", "anthropic"): {"prompt": 3.0, "completion": 15.0},
    ("claude-sonnet-4", "anthropic"): {"prompt": 3.0, "completion": 15.0},
    ("claude-3-haiku", "anthropic"): {"prompt": 0.25, "completion": 1.25},
    # Google Gemini
    ("gemini-pro", "google"): {"prompt": 0.5, "completion": 1.5},
    ("gemini-pro", "gemini"): {"prompt": 0.5, "completion": 1.5},
    ("gemini-1.5-pro", "google"): {"prompt": 1.25, "completion": 5.0},
    ("gemini-1.5-pro", "gemini"): {"prompt": 1.25, "completion": 5.0},
    ("gemini-1.5-flash", "google"): {"prompt": 0.075, "completion": 0.30},
    ("gemini-2.0-flash", "google"): {"prompt": 0.10, "completion": 0.40},
    ("gemini-2.0-flash", "gemini"): {"prompt": 0.10, "completion": 0.40},
    # Local Ollama (zero external API cost)
    ("llama3.2", "ollama"): {"prompt": 0.0, "completion": 0.0},
}

_SK_KEY_RE = re.compile(r"sk-[A-Za-z0-9_-]+")
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")
_AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_GH_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD_RE = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")


def _redact_sensitive(text: str) -> str:
    """Redact API keys, Bearer tokens, email addresses, SSNs, and card numbers from span tags."""
    redacted = _SK_KEY_RE.sub("sk-***REDACTED***", text)
    redacted = _BEARER_RE.sub("Bearer ***REDACTED***", redacted)
    redacted = _AWS_KEY_RE.sub("AKIA***REDACTED***", redacted)
    redacted = _GH_TOKEN_RE.sub("gh_***REDACTED***", redacted)
    redacted = _EMAIL_RE.sub("***@***.***", redacted)
    redacted = _SSN_RE.sub("***-**-****", redacted)
    redacted = _CARD_RE.sub("****-****-****-****", redacted)
    return redacted


def _extract_token_counts(usage: Any) -> tuple[int, int, int]:
    """Extract ``(prompt_tokens, completion_tokens, total_tokens)`` across adapter schemas."""
    prompt_tokens = int(
        getattr(usage, "prompt_tokens", None)
        or getattr(usage, "input_tokens", None)
        or 0
    )
    completion_tokens = int(
        getattr(usage, "completion_tokens", None)
        or getattr(usage, "output_tokens", None)
        or 0
    )
    total_tokens = int(
        getattr(usage, "total_tokens", None)
        or (prompt_tokens + completion_tokens)
    )
    return prompt_tokens, completion_tokens, total_tokens


def _calculate_cost(model: str, provider: str, usage: Any) -> float:
    """Compute estimated USD cost for a traced model API call."""
    prompt_tokens, completion_tokens, _ = _extract_token_counts(usage)
    model_norm = model.strip().lower()
    provider_norm = provider.strip().lower()

    rates = _MODEL_PRICING_PER_1M.get((model_norm, provider_norm))
    if rates is None:
        # Longest-prefix fallback (so "gpt-4o-2026-01-01" matches "gpt-4o", not "gpt-4")
        candidates = sorted(
            (
                (known_model, known_rates)
                for (known_model, known_provider), known_rates in _MODEL_PRICING_PER_1M.items()
                if known_provider == provider_norm and model_norm.startswith(known_model)
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        if candidates:
            rates = candidates[0][1]
    if rates is None:
        return 0.0

    prompt_cost = (prompt_tokens / 1_000_000.0) * rates["prompt"]
    completion_cost = (completion_tokens / 1_000_000.0) * rates["completion"]
    return round(prompt_cost + completion_cost, 6)


def _attach_args(span: trace.Span, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    for i, arg in enumerate(args):
        span.set_attribute(f"arg.{i}", _redact_sensitive(str(arg)[:100]))
    for key, value in kwargs.items():
        lowered = key.lower()
        if any(tok in lowered for tok in ("key", "secret", "password", "token", "auth")):
            span.set_attribute(f"arg.{key}", "***REDACTED***")
        else:
            span.set_attribute(f"arg.{key}", _redact_sensitive(str(value)[:100]))


def trace_agent_step(
    span_name: str | None = None,
    capture_args: bool = False,
    capture_result: bool = False,
    attributes: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator creating a Jaeger/OpenTelemetry span for an agent execution step.

    Supports both async coroutines and synchronous functions while redacting
    sensitive data from captured arguments and return values.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        resolved_name = span_name or f"{func.__module__}.{func.__name__}"

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            active_tracer = trace.get_tracer(__name__)
            with active_tracer.start_as_current_span(resolved_name) as span:
                run_id = CURRENT_RUN_ID.get()
                if run_id:
                    span.set_attribute("pvs.run_id", run_id)
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(
                            key, _redact_sensitive(value) if isinstance(value, str) else value
                        )
                if capture_args:
                    _attach_args(span, args, kwargs)
                try:
                    result = await func(*args, **kwargs)
                    if capture_result and result is not None:
                        span.set_attribute("result", _redact_sensitive(str(result)[:100]))
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as exc:
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.record_exception(exc)
                    span.set_attribute("error.type", type(exc).__name__)
                    raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            active_tracer = trace.get_tracer(__name__)
            with active_tracer.start_as_current_span(resolved_name) as span:
                run_id = CURRENT_RUN_ID.get()
                if run_id:
                    span.set_attribute("pvs.run_id", run_id)
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(
                            key, _redact_sensitive(value) if isinstance(value, str) else value
                        )
                if capture_args:
                    _attach_args(span, args, kwargs)
                try:
                    result = func(*args, **kwargs)
                    if capture_result and result is not None:
                        span.set_attribute("result", _redact_sensitive(str(result)[:100]))
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as exc:
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.record_exception(exc)
                    span.set_attribute("error.type", type(exc).__name__)
                    raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def trace_model_call(
    model_name: str,
    provider: str,
    capture_tokens: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator tracing LLM API calls with token usage and ``model.cost.usd`` attribution."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            active_tracer = trace.get_tracer(__name__)
            span_name = f"model.{provider}.{model_name}"
            with active_tracer.start_as_current_span(span_name) as span:
                span.set_attribute("model.name", model_name)
                span.set_attribute("model.provider", provider)
                span.set_attribute("model.type", "llm")
                span.set_attribute(GenAIAttributes.SYSTEM, provider)
                span.set_attribute(GenAIAttributes.REQUEST_MODEL, model_name)

                run_id = CURRENT_RUN_ID.get()
                if run_id:
                    span.set_attribute("pvs.run_id", run_id)

                try:
                    result = await func(*args, **kwargs)
                    if capture_tokens and hasattr(result, "usage") and result.usage is not None:
                        prompt_tok, completion_tok, total_tok = _extract_token_counts(result.usage)
                        span.set_attribute("model.tokens.prompt", prompt_tok)
                        span.set_attribute("model.tokens.completion", completion_tok)
                        span.set_attribute("model.tokens.total", total_tok)
                        span.set_attribute(GenAIAttributes.USAGE_INPUT_TOKENS, prompt_tok)
                        span.set_attribute(GenAIAttributes.USAGE_OUTPUT_TOKENS, completion_tok)
                        span.set_attribute(GenAIAttributes.USAGE_TOTAL_TOKENS, total_tok)

                        cost_usd = _calculate_cost(model_name, provider, result.usage)
                        span.set_attribute("model.cost.usd", cost_usd)

                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as exc:
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.record_exception(exc)
                    span.set_attribute("error.type", type(exc).__name__)
                    raise

        return wrapper

    return decorator


def _classify_span_node(span: ReadableSpan) -> str:
    """Map an OpenTelemetry span onto its logical service/component node name."""
    attrs = dict(span.attributes or {})
    provider = attrs.get("model.provider") or attrs.get(GenAIAttributes.SYSTEM)
    if provider:
        return f"model:{provider}"
    if span.name.startswith("http.") or "http.method" in attrs:
        return "api"
    if span.name.startswith("agent.tool.") or "tool.name" in attrs:
        return "tool"
    if span.name.startswith("agent.") or "agent.name" in attrs:
        return f"agent:{attrs.get('agent.name', 'loop')}"
    if "db.system" in attrs or span.name.startswith("database."):
        return f"db:{attrs.get('db.system', 'sql')}"
    if span.name.startswith("sandbox."):
        return "sandbox:docker"
    return str(span.resource.attributes.get("service.name", "product-value-system"))


def build_service_dependency_graph(spans: Sequence[ReadableSpan]) -> dict[str, Any]:
    """Derive a Jaeger-style service dependency graph and cost summary from finished spans."""
    by_span_id: dict[int, ReadableSpan] = {}
    for s in spans:
        if s.context is not None:
            by_span_id[s.context.span_id] = s

    nodes: set[str] = set()
    edge_counts: dict[tuple[str, str], int] = {}
    total_cost_usd = 0.0
    max_duration_ms = 0.0
    error_spans = 0

    for s in spans:
        child_node = _classify_span_node(s)
        nodes.add(child_node)

        attrs = dict(s.attributes or {})
        cost_val = attrs.get("model.cost.usd") or attrs.get(GenAIAttributes.COST_USD)
        if isinstance(cost_val, (int, float)):
            total_cost_usd += float(cost_val)

        if s.start_time is not None and s.end_time is not None:
            dur_ms = (s.end_time - s.start_time) / 1_000_000.0
            if dur_ms > max_duration_ms:
                max_duration_ms = dur_ms

        if s.status.status_code == StatusCode.ERROR:
            error_spans += 1

        if s.parent is not None and s.parent.span_id in by_span_id:
            parent_node = _classify_span_node(by_span_id[s.parent.span_id])
            if parent_node != child_node:
                edge_counts[(parent_node, child_node)] = (
                    edge_counts.get((parent_node, child_node), 0) + 1
                )

    edges = [
        {"parent": src, "child": dst, "call_count": cnt}
        for (src, dst), cnt in sorted(edge_counts.items())
    ]
    return {
        "nodes": sorted(nodes),
        "edges": edges,
        "span_count": len(spans),
        "error_spans": error_spans,
        "total_attributed_cost_usd": round(total_cost_usd, 6),
        "critical_path_ms": round(max_duration_ms, 3),
    }


__all__ = [
    "_calculate_cost",
    "_redact_sensitive",
    "build_service_dependency_graph",
    "trace_agent_step",
    "trace_model_call",
]
