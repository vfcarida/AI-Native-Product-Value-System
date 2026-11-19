"""
Unit tests for AnthropicAdapter (EVO-001).
"""

import json
import os
from typing import Any

import httpx
import pytest

from product_value_system.adapters.anthropic_adapter import (
    AnthropicAdapter,
    ModelError,
    ModelUsage,
    NetworkError,
    RateLimitError,
)
from product_value_system.adapters.model_adapter import (
    Message,
    ModelAdapter,
    Role,
    Tool,
)
from tests.fixtures.mock_sse_server import (
    MockSSEResponse,
    create_anthropic_sse_chunks,
)


class TestAnthropicAdapterInit:
    """Test adapter initialization and protocol compliance."""

    def test_init_with_api_key(self) -> None:
        adapter = AnthropicAdapter(api_key="sk-ant-test123")
        assert adapter.api_key == "sk-ant-test123"
        assert adapter.model == "claude-3-5-sonnet-20241022"
        assert adapter.base_url == "https://api.anthropic.com"

    def test_init_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env123")
        monkeypatch.setenv("ANTHROPIC_DEFAULT_MODEL", "claude-3-haiku-20240307")
        adapter = AnthropicAdapter()
        assert adapter.api_key == "sk-ant-env123"
        assert adapter.model == "claude-3-haiku-20240307"

    def test_init_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key required"):
            AnthropicAdapter()

    def test_implements_model_adapter_protocol(self) -> None:
        adapter = AnthropicAdapter(api_key="sk-ant-test123")
        assert isinstance(adapter, ModelAdapter)


class TestAnthropicAdapterInvoke:
    """Test invoke method, retries, tool conversion, and error handling."""

    @pytest.mark.asyncio
    async def test_invoke_success(self) -> None:
        mock_response_data = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello! How can I help?"}],
            "model": "claude-3-5-sonnet-20241022",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 8},
        }

        def mock_transport(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/messages"
            assert request.headers.get("anthropic-version") == "2023-06-01"
            return httpx.Response(200, json=mock_response_data)

        adapter = AnthropicAdapter(
            api_key="sk-ant-test",
            transport=httpx.MockTransport(mock_transport),
        )

        response = await adapter.invoke([{"role": "user", "content": "Hi"}])

        assert response.content == "Hello! How can I help?"
        assert response.finish_reason == "stop"
        assert response.usage is not None
        assert response.usage.prompt_tokens == 10
        assert response.usage.completion_tokens == 8
        assert response.usage.input_tokens == 10
        assert response.usage.output_tokens == 8
        assert response.usage.total_tokens == 18
        assert response.model == "claude-3-5-sonnet-20241022"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invoke_with_message_dataclasses(self) -> None:
        """Test invoke accepts standard Message dataclasses used by Harness."""
        captured_payload: dict[str, Any] = {}

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal captured_payload
            captured_payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "Analyzed"}],
                    "stop_reason": "max_tokens",
                    "usage": {"input_tokens": 12, "output_tokens": 4},
                },
            )

        adapter = AnthropicAdapter(
            api_key="sk-ant-test",
            transport=httpx.MockTransport(mock_transport),
        )

        response = await adapter.invoke(
            [
                Message(role=Role.SYSTEM, content="Be concise"),
                Message(role=Role.USER, content="Analyze problem"),
            ],
            max_tokens=512,
        )

        assert captured_payload["system"] == "Be concise"
        assert captured_payload["max_tokens"] == 512
        assert captured_payload["messages"] == [{"role": "user", "content": "Analyze problem"}]
        assert response.finish_reason == "length"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invoke_with_system_message(self) -> None:
        """Test that system messages are extracted into top-level system field."""
        captured_payload: dict[str, Any] = {}

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal captured_payload
            captured_payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "Ok"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 5, "output_tokens": 2},
                },
            )

        adapter = AnthropicAdapter(
            api_key="sk-ant-test",
            transport=httpx.MockTransport(mock_transport),
        )

        await adapter.invoke(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hi"},
            ]
        )

        assert captured_payload["system"] == "You are helpful"
        assert len(captured_payload["messages"]) == 1
        assert captured_payload["messages"][0]["role"] == "user"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invoke_rejects_empty_and_invalid_messages(self) -> None:
        """Test input validation rejects empty list or invalid role."""
        adapter = AnthropicAdapter(api_key="sk-ant-test")

        with pytest.raises(ValueError, match="cannot be empty"):
            await adapter.invoke([])

        with pytest.raises(ValueError, match="Invalid message role"):
            await adapter.invoke([{"role": "admin", "content": "nope"}])

        await adapter.close()

    @pytest.mark.asyncio
    async def test_invoke_rate_limit_retry(self) -> None:
        """Test 429 rate limit retries and succeeds on next attempt."""
        attempts: list[int] = []

        def mock_transport(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 2:
                return httpx.Response(429, headers={"Retry-After": "0.01"})
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "Success"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 5, "output_tokens": 3},
                },
            )

        adapter = AnthropicAdapter(
            api_key="sk-ant-test",
            transport=httpx.MockTransport(mock_transport),
            max_retries=3,
        )

        response = await adapter.invoke([{"role": "user", "content": "Test"}])

        assert len(attempts) == 2
        assert response.content == "Success"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invoke_rate_limit_exhausted(self) -> None:
        """Test 429 eventually raises RateLimitError."""

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "0.01"})

        adapter = AnthropicAdapter(
            api_key="sk-ant-test",
            transport=httpx.MockTransport(mock_transport),
            max_retries=2,
        )

        with pytest.raises(RateLimitError) as exc_info:
            await adapter.invoke([{"role": "user", "content": "Test"}])
        assert exc_info.value.retry_after_seconds == 0.01
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invoke_api_error_raises_model_error(self) -> None:
        """Test 4xx/5xx API errors raise ModelError with API message."""

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"error": {"type": "invalid_request_error", "message": "Invalid model"}},
            )

        adapter = AnthropicAdapter(
            api_key="sk-ant-test",
            transport=httpx.MockTransport(mock_transport),
            max_retries=1,
        )

        with pytest.raises(ModelError, match="Invalid model"):
            await adapter.invoke([{"role": "user", "content": "Test"}])
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invoke_network_error_raises_network_error(self) -> None:
        """Test persistent httpx.RequestError raises NetworkError."""

        def mock_transport(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        adapter = AnthropicAdapter(
            api_key="sk-ant-test",
            transport=httpx.MockTransport(mock_transport),
            max_retries=1,
        )

        with pytest.raises(NetworkError, match="Request failed after 1 attempts"):
            await adapter.invoke([{"role": "user", "content": "Test"}])
        await adapter.close()

    @pytest.mark.asyncio
    async def test_invoke_with_tools_openai_and_dataclass(self) -> None:
        """Test tool/function calling conversion and tool_use parsing."""
        captured_payload: dict[str, Any] = {}

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal captured_payload
            captured_payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_123",
                            "name": "get_weather",
                            "input": {"location": "SF"},
                        }
                    ],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 20, "output_tokens": 10},
                },
            )

        adapter = AnthropicAdapter(
            api_key="sk-ant-test",
            transport=httpx.MockTransport(mock_transport),
        )

        response = await adapter.invoke(
            messages=[{"role": "user", "content": "Weather in SF?"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"location": {"type": "string"}},
                        },
                    },
                },
                Tool(
                    name="knowledge.query",
                    description="Query KB",
                    parameters={"type": "object", "properties": {}},
                ),
            ],
        )

        assert "tools" in captured_payload
        assert len(captured_payload["tools"]) == 2
        assert captured_payload["tools"][0]["name"] == "get_weather"
        assert "input_schema" in captured_payload["tools"][0]
        assert captured_payload["tools"][1]["name"] == "knowledge.query"
        assert "input_schema" in captured_payload["tools"][1]

        assert response.finish_reason == "tool_calls"
        assert response.tool_calls is not None
        assert response.tool_calls[0]["name"] == "get_weather"
        assert response.tool_calls[0]["arguments"] == {"location": "SF"}
        assert response.tool_calls[0].name == "get_weather"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_stream_yields_chunk(self) -> None:
        """Test stream() yields incremental ModelChunk items via real SSE parsing."""
        sse_events = create_anthropic_sse_chunks("Streamed response text", "end_turn")

        adapter = AnthropicAdapter(
            api_key="sk-ant-test",
            transport=httpx.MockTransport(lambda _: MockSSEResponse(sse_events)),
        )

        chunks = [c async for c in adapter.stream([{"role": "user", "content": "Hi"}])]
        assert len(chunks) == 4  # 3 word deltas + final message_stop chunk
        assert "".join(c.delta for c in chunks) == "Streamed response text"
        assert "".join(c.content for c in chunks) == "Streamed response text"
        assert chunks[-1].finish_reason == "stop"
        assert chunks[-1].usage is not None
        assert chunks[-1].usage.prompt_tokens == 10
        assert chunks[-1].usage.completion_tokens == 3
        assert chunks[-1].usage.total_tokens == 13
        await adapter.close()

    @pytest.mark.asyncio
    async def test_output_sanitizes_leaked_secrets(self) -> None:
        """Test response content redacts leaked API keys before returning."""
        leaked_key = "sk-ant-" + ("a" * 32)

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": f"Here is a key: {leaked_key}"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 5, "output_tokens": 8},
                },
            )

        adapter = AnthropicAdapter(
            api_key="sk-ant-test",
            transport=httpx.MockTransport(mock_transport),
        )

        response = await adapter.invoke([{"role": "user", "content": "Echo"}])
        assert response.content != ""
        assert leaked_key not in response.content
        assert "[REDACTED]:ANTHROPIC_API_KEY" in response.content
        await adapter.close()

    @pytest.mark.asyncio
    async def test_discovery_agent_works_unmodified_with_anthropic_adapter(self) -> None:
        """Verify DiscoveryAgent runs end-to-end via Harness using AnthropicAdapter."""
        from datetime import UTC, datetime
        from pathlib import Path

        from product_value_system.agents.discovery_agent import DiscoveryAgent
        from product_value_system.contracts.validation import SchemaValidator
        from product_value_system.harness.context import ExecutionContext
        from product_value_system.harness.harness import Harness

        outcome_json = json.dumps(
            {
                "hypothesis": "Instant push alerts reduce payment support tickets by 30%",
                "success_criteria": {
                    "primary_metric": {
                        "name": "Payment support ticket rate",
                        "unit": "percentage",
                        "measurement_method": "Tickets per 1000 transactions",
                    },
                    "baseline": 12.0,
                    "target": 8.4,
                    "guardrails": [
                        {"metric": "Notification latency", "threshold": 5.0, "direction": "below"}
                    ],
                },
                "evidence_requirements": [
                    {
                        "type": "quantitative",
                        "description": "Measure ticket deflection over 30 days",
                        "mandatory": True,
                    }
                ],
            }
        )

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": outcome_json}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 120, "output_tokens": 80},
                },
            )

        adapter = AnthropicAdapter(
            api_key="sk-ant-test",
            transport=httpx.MockTransport(mock_transport),
        )
        schema_dir = Path(__file__).parent.parent.parent / "schemas"
        agent = DiscoveryAgent(schema_validator=SchemaValidator(schema_dir))
        harness = Harness(model_adapter=adapter)
        context = ExecutionContext(run_id="anthropic-agent-compat-001")

        problem = {
            "id": "problem-instant-alerts-001",
            "version": 1,
            "created": datetime.now(UTC).isoformat(),
            "statement": "Users call support because payment alerts arrive too late",
            "context": {
                "user_segment": "Mobile banking users",
                "business_context": "Reduce support cost",
            },
            "constraints": {
                "no_pii": True,
                "synthetic_only": True,
                "max_cost_usd": 5.0,
            },
        }

        outcome = await agent.execute(problem, harness, context)
        assert outcome["id"] == "outcome-instant-alerts-001"
        assert "hypothesis" in outcome
        await adapter.close()


class TestAnthropicAdapterCost:
    """Test cost calculation for Anthropic models."""

    def test_calculate_cost_sonnet(self) -> None:
        adapter = AnthropicAdapter(api_key="sk-ant-test")
        usage = ModelUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        cost = adapter.calculate_cost(usage, "claude-3-5-sonnet-20241022")
        # (1000 / 1M) * $3 + (500 / 1M) * $15 = $0.003 + $0.0075 = $0.0105
        assert abs(cost - 0.0105) < 0.0001

    def test_calculate_cost_opus(self) -> None:
        adapter = AnthropicAdapter(api_key="sk-ant-test")
        usage = ModelUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        cost = adapter.calculate_cost(usage, "claude-3-opus-20240229")
        # (1000 / 1M) * $15 + (500 / 1M) * $75 = $0.015 + $0.0375 = $0.0525
        assert abs(cost - 0.0525) < 0.0001

    def test_calculate_cost_haiku(self) -> None:
        adapter = AnthropicAdapter(api_key="sk-ant-test")
        usage = ModelUsage(
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            total_tokens=2_000_000,
        )
        cost = adapter.calculate_cost(usage, "claude-3-haiku-20240307")
        assert abs(cost - 1.50) < 0.0001


@pytest.mark.unit
@pytest.mark.asyncio
async def test_anthropic_stream_parses_events() -> None:
    """Test Anthropic SSE parsing with typed events and stop_reason mapping."""
    sse_events = create_anthropic_sse_chunks("Test response here", "end_turn", input_tokens=15)
    captured_payload: dict[str, Any] = {}

    def mock_transport(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content)
        return MockSSEResponse(sse_events)

    adapter = AnthropicAdapter(
        api_key="sk-ant-test",
        transport=httpx.MockTransport(mock_transport),
    )

    chunks = [
        c
        async for c in adapter.stream(
            [
                {"role": "system", "content": "Act concisely"},
                {"role": "user", "content": "Hi"},
            ]
        )
    ]

    assert captured_payload["stream"] is True
    assert captured_payload["system"] == "Act concisely"
    assert len(chunks) == 4
    assert "".join(c.delta for c in chunks if c.delta) == "Test response here"
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.prompt_tokens == 15
    assert chunks[-1].usage.completion_tokens == 3
    assert chunks[-1].usage.total_tokens == 18
    await adapter.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_anthropic_stream_handles_malformed_json() -> None:
    """Test Anthropic SSE stream skips malformed JSON lines and continues."""
    sse_events = [
        "event: message_start",
        'data: {"type":"message_start","message":{"id":"msg_1","usage":{"input_tokens":5}}}',
        "event: content_block_delta",
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
        "event: content_block_delta",
        "data: {malformed json}",
        "event: content_block_delta",
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" Claude"}}',
        "event: message_delta",
        'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"},'
        '"usage":{"output_tokens":2}}',
        "event: message_stop",
        'data: {"type":"message_stop"}',
    ]

    adapter = AnthropicAdapter(
        api_key="sk-ant-test",
        transport=httpx.MockTransport(lambda _: MockSSEResponse(sse_events)),
    )

    chunks = [c async for c in adapter.stream([{"role": "user", "content": "Hi"}])]
    assert "".join(c.delta for c in chunks) == "Hello Claude"
    assert chunks[-1].finish_reason == "length"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens == 7
    await adapter.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_anthropic_stream_handles_api_and_sse_errors() -> None:
    """Test HTTP errors and SSE error events raise ModelError."""
    http_err_adapter = AnthropicAdapter(
        api_key="sk-ant-test",
        max_retries=1,
        transport=httpx.MockTransport(
            lambda _: MockSSEResponse(['{"error":{"message":"Bad request"}}'], status_code=400)
        ),
    )
    with pytest.raises(ModelError, match="Anthropic API error"):
        _ = [c async for c in http_err_adapter.stream([{"role": "user", "content": "Hi"}])]
    await http_err_adapter.close()

    sse_err_events = [
        "event: error",
        'data: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
    ]
    sse_err_adapter = AnthropicAdapter(
        api_key="sk-ant-test",
        max_retries=1,
        transport=httpx.MockTransport(lambda _: MockSSEResponse(sse_err_events)),
    )
    with pytest.raises(ModelError, match="Overloaded"):
        _ = [c async for c in sse_err_adapter.stream([{"role": "user", "content": "Hi"}])]
    await sse_err_adapter.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_anthropic_stream_handles_rate_limit_and_network_errors() -> None:
    """Test 429 retry and network error translation in Anthropic streaming."""
    attempts: list[int] = []

    def retry_then_ok(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return MockSSEResponse(
                ["rate limited"], status_code=429, headers={"Retry-After": "0.01"}
            )
        return MockSSEResponse(create_anthropic_sse_chunks("Back online"))

    adapter = AnthropicAdapter(
        api_key="sk-ant-test",
        max_retries=2,
        transport=httpx.MockTransport(retry_then_ok),
    )
    chunks = [c async for c in adapter.stream([{"role": "user", "content": "Hi"}])]
    assert len(attempts) == 2
    assert "".join(c.delta for c in chunks) == "Back online"
    await adapter.close()

    def drop_connection(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection reset", request=request)

    net_adapter = AnthropicAdapter(
        api_key="sk-ant-test",
        max_retries=1,
        transport=httpx.MockTransport(drop_connection),
    )
    with pytest.raises(NetworkError, match="Stream connection failed"):
        _ = [c async for c in net_adapter.stream([{"role": "user", "content": "Hi"}])]
    await net_adapter.close()


@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="No Anthropic API key",
)
class TestAnthropicAdapterReal:
    """Integration tests with real Anthropic API."""

    @pytest.mark.asyncio
    async def test_real_invoke(self) -> None:
        """Test real API call."""
        adapter = AnthropicAdapter()

        response = await adapter.invoke(
            [{"role": "user", "content": "Say 'test successful' and nothing else"}],
            max_tokens=10,
        )

        assert "test successful" in response.content.lower()
        assert response.usage is not None
        assert response.usage.total_tokens > 0

        cost = adapter.calculate_cost(response.usage)
        assert cost > 0

        await adapter.close()
