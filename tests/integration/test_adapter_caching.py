"""
Integration tests for transparent PromptCache integration in OpenAI & Anthropic adapters (EVO-025).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from product_value_system.adapters.anthropic_adapter import AnthropicAdapter
from product_value_system.adapters.openai_adapter import OpenAIModelAdapter
from product_value_system.caching import (
    MemoryCacheBackend,
    PromptCache,
    SQLiteCacheBackend,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_adapter_caching_hits_and_high_temp_bypass() -> None:
    """OpenAIModelAdapter serves cached deterministic responses with 1 HTTP call and full Usage."""
    http_calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(
            200,
            json={
                "id": f"chatcmpl-{http_calls}",
                "model": "gpt-4o",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": f"OpenAI answer #{http_calls}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 25, "completion_tokens": 12, "total_tokens": 37},
            },
        )

    cache = PromptCache(backend=MemoryCacheBackend(), enable_semantic_matching=False)
    adapter = OpenAIModelAdapter(
        api_key="test-openai-key-placeholder",
        model="gpt-4o",
        transport=httpx.MockTransport(_handler),
        cache=cache,
    )

    msgs = [{"role": "user", "content": "Generate unit tests for add(a, b)"}]
    r1 = await adapter.invoke(msgs, temperature=0.0, max_tokens=200)
    r2 = await adapter.invoke(msgs, temperature=0.0, max_tokens=200)

    assert http_calls == 1
    assert r1.content == "OpenAI answer #1"
    assert r2.content == "OpenAI answer #1"
    assert r2.usage is not None
    assert r2.usage.total_tokens == 37  # Token accounting preserved on cache hit!
    assert cache.hits == 1
    assert cache.misses == 1

    # Non-deterministic call (temperature=0.7) bypasses cache
    r3 = await adapter.invoke(msgs, temperature=0.7, max_tokens=200)
    assert http_calls == 2
    assert r3.content == "OpenAI answer #2"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_anthropic_adapter_caching_preserves_usage_and_tool_calls() -> None:
    """AnthropicAdapter caches deterministic calls including ToolCalls and Usage."""
    http_calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(
            200,
            json={
                "id": f"msg_{http_calls}",
                "type": "message",
                "role": "assistant",
                "model": "claude-3-5-sonnet-20241022",
                "content": [
                    {"type": "text", "text": "Checking telemetry."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "query_metrics",
                        "input": {"metric": "p95_latency"},
                    },
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 60, "output_tokens": 22},
            },
        )

    cache = PromptCache(backend=MemoryCacheBackend(), enable_semantic_matching=False)
    adapter = AnthropicAdapter(
        api_key="sk-ant-" + "test-placeholder-key",
        model="claude-3-5-sonnet-20241022",
        transport=httpx.MockTransport(_handler),
        cache=cache,
    )

    msgs = [{"role": "user", "content": "Query checkout latency metrics"}]
    r1 = await adapter.invoke(msgs, temperature=0.1, max_tokens=500)
    r2 = await adapter.invoke(msgs, temperature=0.1, max_tokens=500)

    assert http_calls == 1
    assert r1.content == r2.content == "Checking telemetry."
    assert r2.usage is not None and r2.usage.total_tokens == 82
    assert r2.tool_calls is not None and len(r2.tool_calls) == 1
    assert r2.tool_calls[0].name == "query_metrics"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_realistic_agent_workload_achieves_over_50_percent_hit_rate(tmp_path: Path) -> None:
    """Realistic repeated + semantically similar agent workload exceeds 50% cache hit rate."""
    http_calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(
            200,
            json={
                "id": f"chatcmpl-{http_calls}",
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"Generated tests #{http_calls}",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
        )

    cache = PromptCache(
        backend=SQLiteCacheBackend(tmp_path / "workload.db"),
        similarity_threshold=0.85,
        enable_semantic_matching=True,
    )
    adapter = OpenAIModelAdapter(
        api_key="test-openai-key-placeholder",
        model="gpt-4o-mini",
        transport=httpx.MockTransport(_handler),
        cache=cache,
    )

    prompts = [
        # 5 distinct seed prompts
        "Generate unit tests for function def add(a, b): return a + b",
        "Generate unit tests for function def multiply(a, b): return a * b",
        "Review specification for PIX transfer latency SLA requirements",
        "Extract customer pain points from checkout interview transcript",
        "Validate baseline normality and statistical power for experiment",
        # 10 repeated / slightly varied prompts across agent retries and review passes
        "Generate unit tests for function def add(a, b): return a + b",
        "Generate unit tests for function def add(x, y): return x + y",
        "Generate unit tests for function def multiply(a, b): return a * b",
        "Generate unit tests for function def multiply(x, y): return x * y",
        "Review specification for PIX transfer latency SLA requirements",
        "Review specification for PIX transfer latency SLA requirements",
        "Extract customer pain points from checkout interview transcript",
        "Extract customer pain points from checkout interview transcript",
        "Validate baseline normality and statistical power for experiment",
        "Validate baseline normality and statistical power for experiment",
    ]

    for prompt in prompts:
        await adapter.invoke([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=400)

    stats = cache.get_stats()
    assert stats["total_requests"] == 15
    assert stats["hit_rate"] > 0.50, f"Expected >50% hit rate, got {stats['hit_rate']:.1%}"
    assert http_calls <= 6
    assert stats["tokens_saved"] >= 1350


@pytest.mark.integration
@pytest.mark.asyncio
async def test_model_invalidation_forces_fresh_api_call() -> None:
    """Invalidating a model version in PromptCache forces the next adapter invoke to call API."""
    http_calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(
            200,
            json={
                "id": f"chatcmpl-{http_calls}",
                "model": "gpt-4o",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": f"v{http_calls}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    cache = PromptCache(backend=MemoryCacheBackend(), enable_semantic_matching=False)
    adapter = OpenAIModelAdapter(
        api_key="test-openai-key-placeholder",
        model="gpt-4o",
        transport=httpx.MockTransport(_handler),
        cache=cache,
    )

    msgs = [{"role": "user", "content": "Draft ADR"}]
    assert (await adapter.invoke(msgs, temperature=0.0)).content == "v1"
    assert (await adapter.invoke(msgs, temperature=0.0)).content == "v1"
    assert http_calls == 1

    await cache.invalidate_model("gpt-4o")
    assert (await adapter.invoke(msgs, temperature=0.0)).content == "v2"
    assert http_calls == 2
