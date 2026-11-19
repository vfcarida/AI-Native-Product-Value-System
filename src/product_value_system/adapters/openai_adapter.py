"""
OpenAI model adapter implementation.
"""

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

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

logger = logging.getLogger(__name__)


class OpenAIModelAdapter:
    """
    Adapter for OpenAI API (gpt-4, gpt-4-turbo, gpt-3.5-turbo, etc.).

    Handles:
    - Rate limiting with exponential backoff
    - Retries for transient failures
    - Real Server-Sent Events (SSE) streaming
    - Tool/function calling
    - Token usage tracking
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        Initialize OpenAI adapter.

        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            model: Model identifier
            base_url: API base URL
            timeout: Request timeout in seconds
            max_retries: Max retry attempts for transient failures
            transport: Optional custom httpx transport (for testing)
            client: Optional pre-configured httpx.AsyncClient (for testing)
        """
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key required (set OPENAI_API_KEY env var)")

        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._secret_detector = SecretDetector()

        if client is not None:
            self._client = client
        else:
            client_kwargs: dict[str, Any] = {
                "base_url": self.base_url,
                "headers": {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
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
        messages: list[Message | dict[str, Any]],
        tools: list[Tool | dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Invoke OpenAI chat completion API."""
        payload = self._build_payload(messages, tools, temperature, max_tokens, **kwargs)

        for attempt in range(self.max_retries):
            try:
                response = await self._client.post("/chat/completions", json=payload)

                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", 5))
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(retry_after)
                        continue
                    raise RateLimitError(retry_after)

                if response.status_code >= 400:
                    safe_text = self._secret_detector.redact_secrets(response.text)
                    raise ModelError(f"API error: {response.status_code} {safe_text}")

                data: dict[str, Any] = response.json()
                return self._parse_response(data)

            except httpx.RequestError as e:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue
                raise NetworkError(f"Request failed: {e}") from e

        raise ModelError("Max retries exceeded")

    async def stream(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[Tool | dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelChunk]:
        """
        Stream model response with real Server-Sent Events parsing.

        This method provides true streaming with incremental token delivery.
        Content arrives in chunks (typically word-by-word), improving
        perceived responsiveness.

        Example:
            async for chunk in adapter.stream(messages):
                print(chunk.delta, end="", flush=True)
            print(f"\\nUsage: {chunk.usage}")

        Note: Final chunk contains usage statistics and finish_reason.

        Raises:
            RateLimitError: Rate limit exceeded (HTTP 429)
            ModelError: API error or stream error
            NetworkError: Connection dropped before or during streaming
        """
        payload = self._build_payload(messages, tools, temperature, max_tokens, **kwargs)
        payload["stream"] = True

        accumulated_usage: ModelUsage | None = None
        finish_reason: str | None = None
        yielded_any = False
        yielded_final = False

        for attempt in range(self.max_retries):
            try:
                async with self._client.stream(
                    "POST", "/chat/completions", json=payload
                ) as response:
                    if response.status_code == 429:
                        retry_after = float(response.headers.get("Retry-After", 5))
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(retry_after)
                            continue
                        raise RateLimitError(retry_after)

                    if response.status_code >= 400:
                        error_bytes = await response.aread()
                        safe_error = self._secret_detector.redact_secrets(
                            error_bytes.decode("utf-8", errors="replace")
                        )
                        raise ModelError(f"OpenAI API error: {response.status_code} {safe_error}")

                    async for raw_line in response.aiter_lines():
                        line = raw_line.strip()
                        if not line or line.startswith(":"):
                            continue

                        if line == "data: [DONE]":
                            break

                        if not line.startswith("data: "):
                            continue

                        data_str = line[6:]
                        try:
                            data: dict[str, Any] = json.loads(data_str)
                        except json.JSONDecodeError:
                            logger.warning("Skipping malformed OpenAI SSE JSON payload")
                            continue

                        usage_data = data.get("usage")
                        if isinstance(usage_data, dict):
                            prompt_tokens = int(usage_data.get("prompt_tokens", 0))
                            completion_tokens = int(usage_data.get("completion_tokens", 0))
                            total_tokens = int(
                                usage_data.get("total_tokens", prompt_tokens + completion_tokens)
                            )
                            accumulated_usage = ModelUsage(
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                total_tokens=total_tokens,
                            )

                        choices = data.get("choices", [])
                        if not choices:
                            if accumulated_usage is not None and not yielded_final:
                                yielded_any = True
                                yielded_final = True
                                yield ModelChunk(
                                    delta="",
                                    finish_reason=finish_reason or "stop",
                                    usage=accumulated_usage,
                                )
                            continue

                        choice = choices[0]
                        delta = choice.get("delta", {})
                        content_delta = str(delta.get("content") or "")
                        chunk_finish = choice.get("finish_reason")
                        if chunk_finish:
                            finish_reason = str(chunk_finish)

                        if content_delta or chunk_finish:
                            yielded_any = True
                            if chunk_finish:
                                yielded_final = True
                            yield ModelChunk(
                                delta=content_delta,
                                finish_reason=finish_reason if chunk_finish else None,
                                usage=accumulated_usage if chunk_finish else None,
                            )

                if not yielded_final and (yielded_any or accumulated_usage is not None):
                    yield ModelChunk(
                        delta="",
                        finish_reason=finish_reason or "stop",
                        usage=accumulated_usage
                        or ModelUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
                    )
                return

            except httpx.RequestError as exc:
                if not yielded_any and attempt < self.max_retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue
                raise NetworkError(f"Stream connection failed: {exc}") from exc

    def _build_payload(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[Tool | dict[str, Any]] | None,
        temperature: float,
        max_tokens: int | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build OpenAI API request payload."""
        formatted_messages: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, Message):
                formatted_messages.append({"role": msg.role.value, "content": msg.content})
            elif isinstance(msg, dict):
                role_val = msg.get("role", "user")
                role_str = role_val.value if isinstance(role_val, Role) else str(role_val)
                formatted_messages.append(
                    {"role": role_str, "content": str(msg.get("content", ""))}
                )
            else:
                raise ValueError(f"Unsupported message type: {type(msg)}")

        payload: dict[str, Any] = {
            "model": kwargs.pop("model", self.model),
            "messages": formatted_messages,
            "temperature": temperature,
        }

        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

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

    def _parse_response(self, data: dict[str, Any]) -> ModelResponse:
        """Parse OpenAI API response."""
        choice = data["choices"][0]
        message = choice["message"]

        content = message.get("content") or ""

        tool_calls = None
        if "tool_calls" in message and message["tool_calls"]:
            tool_calls = [
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                )
                for tc in message["tool_calls"]
            ]

        usage = None
        if "usage" in data:
            u = data["usage"]
            usage = Usage(
                input_tokens=u["prompt_tokens"],
                output_tokens=u["completion_tokens"],
                total_tokens=u["total_tokens"],
            )

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            model=data.get("model"),
        )

    async def close(self) -> None:
        """Close underlying HTTP client."""
        await self._client.aclose()


OpenAIAdapter = OpenAIModelAdapter
