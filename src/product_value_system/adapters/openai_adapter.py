"""
OpenAI model adapter implementation.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from product_value_system.adapters.model_adapter import (
    Message,
    ModelChunk,
    ModelError,
    ModelResponse,
    RateLimitError,
    Tool,
    ToolCall,
    Usage,
)


class OpenAIModelAdapter:
    """
    Adapter for OpenAI API (gpt-4, gpt-3.5-turbo, etc.).

    Handles:
    - Rate limiting with exponential backoff
    - Retries for transient failures
    - Tool/function calling
    - Token usage tracking
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4",
        base_url: str = "https://api.openai.com/v1",
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        Initialize OpenAI adapter.

        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            model: Model identifier
            base_url: API base URL
            max_retries: Max retry attempts for transient failures
            client: Optional pre-configured httpx.AsyncClient (for testing)
        """
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key required (set OPENAI_API_KEY env var)")

        self.model = model
        self.base_url = base_url
        self.max_retries = max_retries

        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0),
        )

    async def invoke(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
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
                    raise ModelError(f"API error: {response.status_code} {response.text}")

                data: dict[str, Any] = response.json()
                return self._parse_response(data)

            except httpx.RequestError as e:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue
                raise ModelError(f"Request failed: {e}") from e

        raise ModelError("Max retries exceeded")

    async def stream(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelChunk]:
        """Stream OpenAI response."""
        response = await self.invoke(messages, tools, temperature, max_tokens, **kwargs)
        yield ModelChunk(content=response.content, finish_reason=response.finish_reason)

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[Tool] | None,
        temperature: float,
        max_tokens: int | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build OpenAI API request payload."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": msg.role.value, "content": msg.content} for msg in messages
            ],
            "temperature": temperature,
        }

        if max_tokens:
            payload["max_tokens"] = max_tokens

        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]

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
