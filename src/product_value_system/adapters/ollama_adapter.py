"""
Native Ollama local LLM model adapter implementation (v1.0).

Speaks the Ollama ``/api/chat`` REST API directly over ``httpx`` with:
- Zero mandatory API key for local ``http://localhost:11434`` inference (optional bearer
  token for authenticated self-hosted gateways)
- Native tool/function calling (``tools`` / ``message.tool_calls``)
- Real newline-delimited JSON (NDJSON) streaming
- Accurate ``prompt_eval_count`` / ``eval_count`` token telemetry and ``$0.00`` local cost
- Optional deterministic ``PromptCache`` integration and transient retry handling
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx

from product_value_system.adapters.model_adapter import (
    Message,
    ModelChunk,
    ModelError,
    ModelResponse,
    ModelUsage,
    NetworkError,
    RateLimitError,
    Role,
    Tool,
    ToolCall,
    Usage,
)
from product_value_system.security.secret_detection import SecretDetector

if TYPE_CHECKING:
    from product_value_system.caching.prompt_cache import PromptCache

logger = logging.getLogger(__name__)


class OllamaModelAdapter:
    """Native adapter for local or self-hosted Ollama models (llama3.2, qwen2.5, mistral, etc.)."""

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str | None = None,
        *,
        api_key: str | None = None,
        keep_alive: str | int | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        cost_per_1m_input: float = 0.0,
        cost_per_1m_output: float = 0.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        cache: PromptCache | None = None,
        cache_max_temperature: float = 0.3,
    ) -> None:
        resolved_base = (
            base_url
            or os.environ.get("OLLAMA_BASE_URL")
            or os.environ.get("OLLAMA_HOST")
            or "http://localhost:11434"
        ).strip()
        if not resolved_base.startswith(("http://", "https://")):
            resolved_base = f"http://{resolved_base}"

        self.model = model
        self.base_url = resolved_base.rstrip("/")
        self.api_key = api_key or os.environ.get("OLLAMA_API_KEY")
        self.keep_alive = keep_alive
        self.timeout = float(timeout)
        self.max_retries = max(1, int(max_retries))
        self.cost_per_1m_input = max(0.0, float(cost_per_1m_input))
        self.cost_per_1m_output = max(0.0, float(cost_per_1m_output))
        self.cache = cache
        self.cache_max_temperature = float(cache_max_temperature)
        self._secret_detector = SecretDetector()

        if client is not None:
            self._client = client
        else:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            client_kwargs: dict[str, Any] = {
                "base_url": self.base_url,
                "headers": headers,
                "timeout": httpx.Timeout(timeout=self.timeout),
            }
            if transport is not None:
                client_kwargs["transport"] = transport
            self._client = httpx.AsyncClient(**client_kwargs)

    @property
    def client(self) -> httpx.AsyncClient:
        """Return underlying HTTP client."""
        return self._client

    async def invoke(
        self,
        messages: list[Message] | list[dict[str, Any]],
        tools: list[Tool] | list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Invoke Ollama ``/api/chat`` endpoint (non-streaming)."""
        model_name = str(kwargs.pop("model", None) or self.model)
        should_cache = self.cache is not None and temperature < self.cache_max_temperature
        if should_cache and self.cache is not None:
            from product_value_system.caching.prompt_cache import deserialize_model_response

            cached = await self.cache.get(
                messages=messages,
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
            )
            if cached is not None:
                logger.info("PromptCache hit for Ollama model %s", model_name)
                return deserialize_model_response(cached)

        payload = self._build_payload(
            messages, tools, temperature, max_tokens, model_name=model_name, stream=False, **kwargs
        )

        for attempt in range(self.max_retries):
            try:
                response = await self._client.post("/api/chat", json=payload)

                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", 5))
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(retry_after)
                        continue
                    raise RateLimitError(retry_after)

                if response.status_code >= 500 and attempt < self.max_retries - 1:
                    await asyncio.sleep(float(2**attempt))
                    continue

                if response.status_code >= 400:
                    safe_text = self._secret_detector.redact_secrets(response.text)
                    raise ModelError(f"Ollama API error: {response.status_code} {safe_text}")

                data: dict[str, Any] = response.json()
                model_response = self._parse_response(data, fallback_model=model_name)

                if should_cache and self.cache is not None:
                    from product_value_system.caching.prompt_cache import serialize_model_response

                    await self.cache.set(
                        messages=messages,
                        model=model_name,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response=serialize_model_response(model_response),
                        tools=tools,
                    )
                return model_response

            except httpx.RequestError as exc:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(float(2**attempt))
                    continue
                raise NetworkError(f"Ollama request failed: {exc}") from exc

        raise ModelError("Max retries exceeded for Ollama request")

    async def stream(
        self,
        messages: list[Message] | list[dict[str, Any]],
        tools: list[Tool] | list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelChunk]:
        """Stream Ollama NDJSON chunks from ``/api/chat``."""
        model_name = str(kwargs.pop("model", None) or self.model)
        payload = self._build_payload(
            messages, tools, temperature, max_tokens, model_name=model_name, stream=True, **kwargs
        )

        for attempt in range(self.max_retries):
            yielded_any = False
            yielded_final = False
            accumulated_usage: ModelUsage | None = None
            finish_reason: str | None = None

            try:
                async with self._client.stream("POST", "/api/chat", json=payload) as response:
                    if response.status_code == 429:
                        retry_after = float(response.headers.get("Retry-After", 5))
                        if not yielded_any and attempt < self.max_retries - 1:
                            await asyncio.sleep(retry_after)
                            continue
                        raise RateLimitError(retry_after)

                    if response.status_code >= 400:
                        raw_err = (await response.aread()).decode("utf-8", errors="replace")
                        safe_err = self._secret_detector.redact_secrets(raw_err)
                        raise ModelError(f"Ollama stream error: {response.status_code} {safe_err}")

                    async for raw_line in response.aiter_lines():
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        msg = data.get("message") or {}
                        content_delta = str(msg.get("content") or "")
                        done = bool(data.get("done", False))

                        if done:
                            in_tok = int(data.get("prompt_eval_count") or 0)
                            out_tok = int(data.get("eval_count") or 0)
                            accumulated_usage = ModelUsage(
                                input_tokens=in_tok,
                                output_tokens=out_tok,
                                total_tokens=in_tok + out_tok,
                            )
                            finish_reason = str(data.get("done_reason") or "stop")
                            yielded_any = True
                            yielded_final = True
                            yield ModelChunk(
                                delta=content_delta,
                                finish_reason=finish_reason,
                                usage=accumulated_usage,
                            )
                            break

                        if content_delta:
                            yielded_any = True
                            yield ModelChunk(delta=content_delta)

                if not yielded_final and (yielded_any or accumulated_usage is not None):
                    yield ModelChunk(
                        delta="",
                        finish_reason=finish_reason or "stop",
                        usage=accumulated_usage
                        or ModelUsage(input_tokens=0, output_tokens=0, total_tokens=0),
                    )
                return

            except httpx.RequestError as exc:
                if not yielded_any and attempt < self.max_retries - 1:
                    await asyncio.sleep(float(2**attempt))
                    continue
                raise NetworkError(f"Ollama stream connection failed: {exc}") from exc

    def _build_payload(
        self,
        messages: list[Message] | list[dict[str, Any]],
        tools: list[Tool] | list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int | None,
        *,
        model_name: str,
        stream: bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build Ollama ``/api/chat`` JSON payload."""
        formatted_messages: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, Message):
                formatted_messages.append({"role": msg.role.value, "content": msg.content})
            elif isinstance(msg, dict):
                raw_role = msg.get("role", "user")
                role_str = raw_role.value if isinstance(raw_role, Role) else str(raw_role)
                formatted_messages.append(
                    {"role": role_str, "content": str(msg.get("content", ""))}
                )
            else:
                raise ValueError(f"Unsupported message type: {type(msg)}")

        options: dict[str, Any] = dict(kwargs.pop("options", None) or {})
        options.setdefault("temperature", float(temperature))
        if max_tokens is not None:
            options["num_predict"] = int(max_tokens)

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": formatted_messages,
            "stream": stream,
            "options": options,
        }
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive

        if tools:
            formatted_tools: list[dict[str, Any]] = []
            for tool in tools:
                if isinstance(tool, Tool):
                    formatted_tools.append(
                        {
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": tool.description,
                                "parameters": tool.parameters,
                            },
                        }
                    )
                elif isinstance(tool, dict):
                    formatted_tools.append(tool)
            payload["tools"] = formatted_tools

        payload.update(kwargs)
        return payload

    def _parse_response(self, data: dict[str, Any], *, fallback_model: str) -> ModelResponse:
        """Parse Ollama ``/api/chat`` JSON response."""
        message = data.get("message") or {}
        content = str(message.get("content") or "")

        tool_calls: list[ToolCall] | None = None
        raw_tcs = message.get("tool_calls")
        if isinstance(raw_tcs, list) and raw_tcs:
            parsed_calls: list[ToolCall] = []
            for idx, tc in enumerate(raw_tcs):
                fn = tc.get("function") or {}
                raw_args = fn.get("arguments", {})
                args_val = (
                    json.dumps(raw_args) if isinstance(raw_args, (dict, list)) else str(raw_args)
                )
                parsed_calls.append(
                    ToolCall(
                        id=str(tc.get("id") or f"ollama_call_{idx}"),
                        name=str(fn.get("name", "")),
                        arguments=args_val,
                    )
                )
            tool_calls = parsed_calls

        in_tok = int(data.get("prompt_eval_count") or 0)
        out_tok = int(data.get("eval_count") or 0)
        usage = Usage(input_tokens=in_tok, output_tokens=out_tok, total_tokens=in_tok + out_tok)

        finish_reason = "tool_calls" if tool_calls else str(data.get("done_reason") or "stop")
        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
            model=str(data.get("model") or fallback_model),
        )

    def calculate_cost(self, usage: Usage, model: str | None = None) -> float:
        """Calculate inference cost in USD ($0.00 by default for local Ollama)."""
        _ = model
        return (usage.input_tokens / 1_000_000) * self.cost_per_1m_input + (
            usage.output_tokens / 1_000_000
        ) * self.cost_per_1m_output

    async def close(self) -> None:
        """Close underlying HTTP client."""
        await self._client.aclose()


OllamaAdapter = OllamaModelAdapter
