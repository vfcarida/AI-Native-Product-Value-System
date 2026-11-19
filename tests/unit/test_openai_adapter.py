"""
Unit tests for OpenAIModelAdapter / OpenAIAdapter SSE streaming (EVO-002).
"""

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from product_value_system.adapters.model_adapter import (
    Message,
    ModelError,
    ModelUsage,
    NetworkError,
    RateLimitError,
    Role,
)
from product_value_system.adapters.openai_adapter import OpenAIAdapter
from product_value_system.harness.budget import Budget, BudgetExceededError
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from tests.fixtures.mock_sse_server import (
    MockSSEResponse,
    create_openai_sse_chunks,
)
from tests.integration_real.conftest import RealIntegrationCostTracker


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_parses_sse() -> None:
    """Test real OpenAI SSE parsing yields multiple chunks and final usage."""
    sse_events = create_openai_sse_chunks("Hello world test", "stop")
    captured_payload: dict[str, Any] = {}

    def mock_transport(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content)
        return MockSSEResponse(sse_events)

    adapter = OpenAIAdapter(
        api_key="sk-test",
        transport=httpx.MockTransport(mock_transport),
    )

    chunks = [c async for c in adapter.stream([{"role": "user", "content": "Hi"}])]

    assert captured_payload["stream"] is True
    assert len(chunks) == 4  # 3 words + final chunk with finish_reason & usage

    full_content = "".join(c.delta for c in chunks)
    assert full_content == "Hello world test"

    final_chunk = chunks[-1]
    assert final_chunk.finish_reason == "stop"
    assert final_chunk.usage is not None
    assert final_chunk.usage.prompt_tokens == 10
    assert final_chunk.usage.completion_tokens == 3
    assert final_chunk.usage.total_tokens == 13
    await adapter.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_accepts_message_dataclasses() -> None:
    """Test OpenAI streaming accepts Message dataclasses."""
    sse_events = create_openai_sse_chunks("Token one two", "length")

    adapter = OpenAIAdapter(
        api_key="sk-test",
        transport=httpx.MockTransport(lambda _: MockSSEResponse(sse_events)),
    )

    chunks = [
        c
        async for c in adapter.stream(
            [
                Message(role=Role.SYSTEM, content="Be brief"),
                Message(role=Role.USER, content="Count"),
            ]
        )
    ]

    assert "".join(c.content for c in chunks) == "Token one two"
    assert chunks[-1].finish_reason == "length"
    await adapter.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_handles_empty_chunks() -> None:
    """Test streaming skips empty deltas while preserving content and finish chunk."""
    sse_events = [
        'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"content":" world"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}',
        "data: [DONE]",
    ]

    adapter = OpenAIAdapter(
        api_key="sk-test",
        transport=httpx.MockTransport(lambda _: MockSSEResponse(sse_events)),
    )

    chunks = [c async for c in adapter.stream([{"role": "user", "content": "Hi"}])]

    non_empty = [c for c in chunks if c.delta or c.finish_reason]
    assert len(non_empty) == 3
    assert "".join(c.delta for c in chunks) == "Hello world"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens == 7
    await adapter.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_handles_malformed_json() -> None:
    """Test streaming skips malformed JSON events and continues."""
    sse_events = [
        'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}',
        "data: {invalid json}",
        'data: {"choices":[{"delta":{"content":" world"},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5}}',
        "data: [DONE]",
    ]

    adapter = OpenAIAdapter(
        api_key="sk-test",
        transport=httpx.MockTransport(lambda _: MockSSEResponse(sse_events)),
    )

    chunks = [c async for c in adapter.stream([{"role": "user", "content": "Hi"}])]

    assert "".join(c.delta for c in chunks) == "Hello world"
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens == 5
    await adapter.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_handles_api_error() -> None:
    """Test HTTP 4xx error during streaming raises ModelError."""
    adapter = OpenAIAdapter(
        api_key="sk-test",
        max_retries=1,
        transport=httpx.MockTransport(
            lambda _: MockSSEResponse(['{"error":"Invalid request"}'], status_code=400)
        ),
    )

    with pytest.raises(ModelError, match="400"):
        _ = [c async for c in adapter.stream([{"role": "user", "content": "Hi"}])]
    await adapter.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_handles_rate_limit_retry_and_exhaustion() -> None:
    """Test 429 rate limit during streaming retries and raises RateLimitError if exhausted."""
    attempts: list[int] = []

    def retry_then_succeed(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return MockSSEResponse(
                ["rate limited"], status_code=429, headers={"Retry-After": "0.01"}
            )
        return MockSSEResponse(create_openai_sse_chunks("Recovered stream"))

    adapter = OpenAIAdapter(
        api_key="sk-test",
        max_retries=2,
        transport=httpx.MockTransport(retry_then_succeed),
    )
    chunks = [c async for c in adapter.stream([{"role": "user", "content": "Hi"}])]
    assert len(attempts) == 2
    assert "".join(c.delta for c in chunks) == "Recovered stream"
    await adapter.close()

    exhausted_adapter = OpenAIAdapter(
        api_key="sk-test",
        max_retries=1,
        transport=httpx.MockTransport(
            lambda _: MockSSEResponse(
                ["rate limited"], status_code=429, headers={"Retry-After": "0.01"}
            )
        ),
    )
    with pytest.raises(RateLimitError):
        _ = [c async for c in exhausted_adapter.stream([{"role": "user", "content": "Hi"}])]
    await exhausted_adapter.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_handles_network_error() -> None:
    """Test network errors during streaming raise NetworkError."""

    def failing_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("Connection dropped mid-stream", request=request)

    adapter = OpenAIAdapter(
        api_key="sk-test",
        max_retries=1,
        transport=httpx.MockTransport(failing_transport),
    )

    with pytest.raises(NetworkError, match="Stream connection failed"):
        _ = [c async for c in adapter.stream([{"role": "user", "content": "Hi"}])]
    await adapter.close()


@pytest.mark.unit
def test_openai_calculate_cost_and_real_cost_tracker() -> None:
    """Test OpenAIAdapter.calculate_cost and RealIntegrationCostTracker budget caps."""
    adapter = OpenAIAdapter(api_key="sk-test", model="gpt-3.5-turbo")
    usage = ModelUsage(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000)
    # (1000/1M)*$0.50 + (1000/1M)*$1.50 = $0.002
    cost = adapter.calculate_cost(usage)
    assert abs(cost - 0.002) < 0.000001

    tracker = RealIntegrationCostTracker(max_per_test=0.05, max_total=0.10)
    tracker.track(0.04)
    assert abs(tracker.spent - 0.04) < 1e-9
    with pytest.raises(RuntimeError, match="exceeded cost budget"):
        tracker.track(0.07)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_harness_records_adapter_cost_and_enforces_budget() -> None:
    """Test Harness.invoke_model records real adapter USD cost and enforces budget."""

    def mock_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gpt-4",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Expensive answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 100_000,
                    "completion_tokens": 50_000,
                    "total_tokens": 150_000,
                },
            },
        )

    adapter = OpenAIAdapter(
        api_key="sk-test",
        model="gpt-4",
        transport=httpx.MockTransport(mock_transport),
    )
    harness = Harness(
        context=ExecutionContext(run_id="cost-enforcement-test"),
        model_adapter=adapter,
        budget=Budget(max_cost_usd=Decimal("1.00")),
    )
    # 100k input * $30/1M = $3.00, 50k output * $60/1M = $3.00 -> $6.00 > $1.00
    with pytest.raises(BudgetExceededError, match="Cost budget exceeded"):
        await harness.invoke_model([{"role": "user", "content": "Hello"}])
    assert harness.budget.current_cost_usd == Decimal("6.0")
    await adapter.close()
