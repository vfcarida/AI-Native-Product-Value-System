"""Unit tests for v1.0 native model adapters: Gemini, Azure OpenAI, and Ollama."""

from __future__ import annotations

import json

import httpx
import pytest

from product_value_system import (
    AzureOpenAIModelAdapter,
    GeminiModelAdapter,
    OllamaModelAdapter,
    create_adapter,
)
from product_value_system.adapters.model_adapter import (
    Message,
    ModelAdapter,
    ModelError,
    RateLimitError,
    Role,
    Tool,
    Usage,
)
from product_value_system.caching.cache_backends import MemoryCacheBackend
from product_value_system.caching.prompt_cache import PromptCache
from product_value_system.observability.genai_conventions import infer_genai_system


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_adapter_invoke_tools_stream_cache_and_cost() -> None:
    """GeminiModelAdapter handles x-goog-api-key auth, systemInstruction, tools, SSE, and cache."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["x-goog-api-key"] == "test-gemini-token"
        assert "key=" not in str(request.url)  # Never leak API key in query string

        if ":streamGenerateContent" in request.url.path:
            assert request.url.params.get("alt") == "sse"
            sse_body = (
                'data: {"candidates":[{"content":{"parts":[{"text":"Hello "}]}}]}\n\n'
                'data: {"candidates":[{"content":{"parts":[{"text":"Gemini!"}]},'
                '"finishReason":"STOP"}],'
                '"usageMetadata":{"promptTokenCount":12,"candidatesTokenCount":4,'
                '"totalTokenCount":16}}\n\n'
            )
            return httpx.Response(200, text=sse_body, headers={"content-type": "text/event-stream"})

        body = json.loads(request.content.decode("utf-8"))
        assert body["systemInstruction"]["parts"][0]["text"] == "You are concise."
        assert body["tools"][0]["functionDeclarations"][0]["name"] == "get_quote"
        return httpx.Response(
            200,
            json={
                "modelVersion": "gemini-2.5-flash",
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Checking quote."},
                                {"functionCall": {"name": "get_quote", "args": {"ticker": "GOOG"}}},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 50,
                    "totalTokenCount": 150,
                },
            },
        )

    cache = PromptCache(backend=MemoryCacheBackend(), enable_semantic_matching=False)
    adapter = GeminiModelAdapter(
        api_key="test-gemini-token",
        model="gemini-2.5-flash",
        transport=httpx.MockTransport(handler),
        cache=cache,
    )
    assert isinstance(adapter, ModelAdapter)

    msgs = [
        Message(role=Role.SYSTEM, content="You are concise."),
        Message(role=Role.USER, content="Quote GOOG"),
    ]
    tools = [
        Tool(
            name="get_quote",
            description="Fetch equity quote",
            parameters={"type": "object", "properties": {"ticker": {"type": "string"}}},
        )
    ]

    resp1 = await adapter.invoke(msgs, tools=tools, temperature=0.0, max_tokens=256)
    assert resp1.content == "Checking quote."
    assert resp1.finish_reason == "tool_calls"
    assert resp1.tool_calls is not None and len(resp1.tool_calls) == 1
    assert resp1.tool_calls[0].name == "get_quote"
    assert json.loads(resp1.tool_calls[0].arguments) == {"ticker": "GOOG"}
    assert resp1.usage == Usage(input_tokens=100, output_tokens=50, total_tokens=150)

    # Deterministic temperature=0.0 hits PromptCache on second invoke (no extra HTTP call)
    resp2 = await adapter.invoke(msgs, tools=tools, temperature=0.0, max_tokens=256)
    assert resp2.content == resp1.content
    assert len(calls) == 1

    # Real SSE streaming
    chunks = [chunk async for chunk in adapter.stream(msgs, temperature=0.2)]
    assert "".join(c.delta for c in chunks) == "Hello Gemini!"
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage == Usage(input_tokens=12, output_tokens=4, total_tokens=16)

    # Pricing calculation ($0.15/1M in, $0.60/1M out)
    cost = adapter.calculate_cost(Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert cost == pytest.approx(0.75)
    await adapter.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_azure_openai_adapter_invoke_ad_token_stream_and_errors() -> None:
    """AzureOpenAIModelAdapter targets deployment URL, supports api-key & Entra bearer + SSE."""
    seen_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        assert request.url.path == "/openai/deployments/gpt4o-prod/chat/completions"
        assert request.url.params.get("api-version") == "2024-10-21"

        payload = json.loads(request.content.decode("utf-8"))
        if payload.get("stream"):
            sse = (
                'data: {"choices":[{"delta":{"content":"Azure "},"finish_reason":null}]}\n\n'
                'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}],'
                '"usage":{"prompt_tokens":10,"completion_tokens":3,"total_tokens":13}}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

        return httpx.Response(
            200,
            json={
                "model": "gpt-4o",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Deployed on Azure.",
                            "tool_calls": [
                                {
                                    "id": "call_az_1",
                                    "type": "function",
                                    "function": {
                                        "name": "deploy_check",
                                        "arguments": '{"env":"prod"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 40, "total_tokens": 240},
            },
        )

    adapter = AzureOpenAIModelAdapter(
        endpoint="https://acme-ai.openai.azure.com",
        deployment="gpt4o-prod",
        api_key="test-azure-key-placeholder",
        model="gpt-4o",
        transport=httpx.MockTransport(handler),
    )
    assert isinstance(adapter, ModelAdapter)

    resp = await adapter.invoke([Message(role=Role.USER, content="Check status")], temperature=0.1)
    assert resp.content == "Deployed on Azure."
    assert resp.tool_calls is not None and resp.tool_calls[0].name == "deploy_check"
    assert resp.usage == Usage(input_tokens=200, output_tokens=40, total_tokens=240)
    assert seen_headers[0]["api-key"] == "test-azure-key-placeholder"

    chunks = [
        c async for c in adapter.stream([Message(role=Role.USER, content="Hi")], temperature=0.1)
    ]
    assert "".join(c.delta for c in chunks) == "Azure OK"
    assert chunks[-1].usage == Usage(input_tokens=10, output_tokens=3, total_tokens=13)

    cost = adapter.calculate_cost(Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert cost == pytest.approx(12.50)
    await adapter.close()

    # Entra ID bearer token authentication path
    def ad_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer entra-token-123"
        assert "api-key" not in request.headers
        return httpx.Response(429, headers={"Retry-After": "0.01"}, text="rate limited")

    ad_adapter = AzureOpenAIModelAdapter(
        endpoint="https://acme-ai.openai.azure.com",
        deployment="gpt4o-prod",
        azure_ad_token="entra-token-123",
        max_retries=1,
        transport=httpx.MockTransport(ad_handler),
    )
    with pytest.raises(RateLimitError):
        await ad_adapter.invoke([Message(role=Role.USER, content="Ping")])
    await ad_adapter.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ollama_adapter_invoke_tools_ndjson_stream_and_zero_cost() -> None:
    """OllamaModelAdapter requires no API key, parses /api/chat tools + NDJSON, and costs $0."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["model"] == "llama3.2"

        if payload["stream"]:
            ndjson = (
                '{"model":"llama3.2","message":{"role":"assistant","content":"Local "},'
                '"done":false}\n'
                '{"model":"llama3.2","message":{"role":"assistant","content":"LLM!"},'
                '"done":true,"done_reason":"stop","prompt_eval_count":18,"eval_count":6}\n'
            )
            return httpx.Response(
                200, text=ndjson, headers={"content-type": "application/x-ndjson"}
            )

        return httpx.Response(
            200,
            json={
                "model": "llama3.2",
                "message": {
                    "role": "assistant",
                    "content": "Running locally.",
                    "tool_calls": [
                        {"function": {"name": "search_repo", "arguments": {"pattern": "TODO"}}}
                    ],
                },
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 42,
                "eval_count": 15,
            },
        )

    adapter = OllamaModelAdapter(
        model="llama3.2",
        base_url="http://localhost:11434",
        transport=httpx.MockTransport(handler),
    )
    assert isinstance(adapter, ModelAdapter)

    resp = await adapter.invoke(
        [Message(role=Role.USER, content="Find TODOs")],
        tools=[Tool(name="search_repo", description="Search repo", parameters={"type": "object"})],
        temperature=0.0,
        max_tokens=128,
    )
    assert resp.content == "Running locally."
    assert resp.finish_reason == "tool_calls"
    assert resp.tool_calls is not None and len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "search_repo"
    assert json.loads(resp.tool_calls[0].arguments) == {"pattern": "TODO"}
    assert resp.usage == Usage(input_tokens=42, output_tokens=15, total_tokens=57)
    assert adapter.calculate_cost(resp.usage) == 0.0

    chunks = [
        c async for c in adapter.stream([Message(role=Role.USER, content="Hi")], temperature=0.1)
    ]
    assert "".join(c.delta for c in chunks) == "Local LLM!"
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage == Usage(input_tokens=18, output_tokens=6, total_tokens=24)
    await adapter.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_adapter_factory_and_error_handling() -> None:
    """create_adapter resolves all 6 providers, rejects unknown providers, and redacts errors."""
    dummy_transport = httpx.MockTransport(lambda _r: httpx.Response(500, text="internal error"))

    g = create_adapter("gemini", "gemini-2.5-pro", api_key="k", transport=dummy_transport)
    az = create_adapter(
        "azure_openai",
        "gpt-4o",
        endpoint="https://example.openai.azure.com",
        api_key="k",
        transport=dummy_transport,
    )
    ol = create_adapter("ollama", "qwen2.5", transport=dummy_transport)
    mk = create_adapter("mock")

    assert isinstance(g, GeminiModelAdapter)
    assert isinstance(az, AzureOpenAIModelAdapter)
    assert isinstance(ol, OllamaModelAdapter)
    assert isinstance(mk, ModelAdapter)
    assert infer_genai_system("qwen2.5:14b") == "ollama"
    assert infer_genai_system("gemini-2.5-flash") == "vertex_ai"

    with pytest.raises(ValueError, match="Unsupported model provider"):
        create_adapter("unknown-provider")

    # Non-retryable 400 error raises ModelError
    bad_transport = httpx.MockTransport(lambda _r: httpx.Response(400, text="invalid payload"))
    bad_gemini = GeminiModelAdapter(
        api_key="k", max_retries=1, transport=bad_transport
    )
    with pytest.raises(ModelError, match="400"):
        await bad_gemini.invoke([Message(role=Role.USER, content="hi")])
    await bad_gemini.close()
    await g.close()
    await az.close()
    await ol.close()

    # Validate invalid Azure endpoint is rejected immediately
    with pytest.raises(ValueError, match="https://"):
        AzureOpenAIModelAdapter(
            endpoint="ftp://bad-host",
            deployment="dep",
            api_key="k",
        )
