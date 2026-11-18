"""
Model adapter unit and integration tests for P003.
"""

import os
import time

import httpx
import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.adapters.model_adapter import (
    Message,
    ModelAdapter,
    ModelError,
    RateLimitError,
    Role,
    Tool,
)
from product_value_system.adapters.openai_adapter import OpenAIModelAdapter


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mock_adapter_basic() -> None:
    """Test mock adapter returns predefined response."""
    adapter: ModelAdapter = MockModelAdapter(responses=["Hello, world!"])

    messages = [Message(role=Role.USER, content="Test")]
    response = await adapter.invoke(messages)

    assert response.content == "Hello, world!"
    assert response.usage is not None
    assert response.usage.total_tokens > 0
    assert response.finish_reason == "stop"
    assert response.model == "mock-model"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mock_adapter_queue() -> None:
    """Test mock adapter uses FIFO queue."""
    adapter = MockModelAdapter(responses=["First", "Second", "Third"])

    messages = [Message(role=Role.USER, content="Test")]

    r1 = await adapter.invoke(messages)
    assert r1.content == "First"

    r2 = await adapter.invoke(messages)
    assert r2.content == "Second"

    r3 = await adapter.invoke(messages)
    assert r3.content == "Third"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mock_adapter_default() -> None:
    """Test mock adapter falls back to default when queue empty."""
    adapter = MockModelAdapter(responses=["First"], default_response="Default")

    messages = [Message(role=Role.USER, content="Test")]

    r1 = await adapter.invoke(messages)
    assert r1.content == "First"

    r2 = await adapter.invoke(messages)
    assert r2.content == "Default"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mock_adapter_stream() -> None:
    """Test mock adapter streaming."""
    adapter = MockModelAdapter(responses=["Hello world"])

    messages = [Message(role=Role.USER, content="Test")]
    chunks: list[str] = []

    async for chunk in adapter.stream(messages):
        chunks.append(chunk.content)

    assert len(chunks) == 2
    assert chunks[0] == "Hello "
    assert chunks[1] == "world "


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mock_adapter_simulates_latency() -> None:
    """Test mock adapter can simulate latency."""
    adapter = MockModelAdapter(simulate_latency_ms=50)
    messages = [Message(role=Role.USER, content="Test")]

    start = time.time()
    await adapter.invoke(messages)
    duration = time.time() - start

    assert duration >= 0.04


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mock_adapter_with_tools() -> None:
    """Test mock adapter handles tools parameter."""
    adapter = MockModelAdapter(responses=["Tool response"])

    messages = [Message(role=Role.USER, content="Test")]
    tools = [
        Tool(
            name="search",
            description="Search for information",
            parameters={"type": "object", "properties": {}},
        )
    ]

    response = await adapter.invoke(messages, tools=tools)
    assert response.content == "Tool response"


@pytest.mark.unit
def test_openai_adapter_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test OpenAI adapter requires API key."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key required"):
        OpenAIModelAdapter()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openai_adapter_parses_response_and_tools() -> None:
    """Test OpenAI adapter builds payload and parses response with mock transport."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "model": "gpt-4",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Synthesized result",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "knowledge.query",
                                        "arguments": '{"query": "fees"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.openai.com/v1", transport=transport)
    adapter = OpenAIModelAdapter(api_key="test-key", client=client)

    tools = [
        Tool(
            name="knowledge.query",
            description="Query knowledge base",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        )
    ]
    response = await adapter.invoke(
        [Message(role=Role.USER, content="Check fees")],
        tools=tools,
        max_tokens=256,
    )

    assert response.content == "Synthesized result"
    assert response.finish_reason == "tool_calls"
    assert response.usage is not None
    assert response.usage.total_tokens == 30
    assert response.tool_calls is not None
    assert response.tool_calls[0].name == "knowledge.query"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openai_adapter_rate_limit_and_error() -> None:
    """Test OpenAI adapter raises RateLimitError on 429 and ModelError on 400."""
    rate_limit_client = httpx.AsyncClient(
        base_url="https://api.openai.com/v1",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(429, headers={"Retry-After": "0.01"})
        ),
    )
    adapter_429 = OpenAIModelAdapter(
        api_key="test-key", max_retries=2, client=rate_limit_client
    )
    with pytest.raises(RateLimitError) as exc_info:
        await adapter_429.invoke([Message(role=Role.USER, content="Test")])
    assert exc_info.value.retry_after_seconds == 0.01

    error_client = httpx.AsyncClient(
        base_url="https://api.openai.com/v1",
        transport=httpx.MockTransport(lambda _: httpx.Response(400, text="Bad Request")),
    )
    adapter_400 = OpenAIModelAdapter(api_key="test-key", max_retries=1, client=error_client)
    with pytest.raises(ModelError, match="400"):
        await adapter_400.invoke([Message(role=Role.USER, content="Test")])


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_openai_adapter_invoke() -> None:
    """Test OpenAI adapter actual API call when OPENAI_API_KEY is available."""
    adapter = OpenAIModelAdapter(model="gpt-3.5-turbo")

    messages = [
        Message(role=Role.SYSTEM, content="You are a helpful assistant."),
        Message(role=Role.USER, content="Say 'test successful'"),
    ]

    response = await adapter.invoke(messages, temperature=0)

    assert response.content is not None
    assert len(response.content) > 0
    assert response.usage is not None
    assert response.usage.total_tokens > 0
    assert response.finish_reason == "stop"
