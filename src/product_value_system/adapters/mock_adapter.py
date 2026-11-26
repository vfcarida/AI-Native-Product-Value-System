"""
Mock model adapter for testing without API calls.
Supports simulated native prompt caching telemetry and structured output tests.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from typing import Any

from product_value_system.adapters.model_adapter import (
    Message,
    ModelChunk,
    ModelResponse,
    Tool,
    Usage,
)


class MockModelAdapter:
    """
    Mock adapter returning predefined responses.

    Useful for:
    - Unit tests
    - Offline development
    - Cost-free experimentation
    """

    def __init__(
        self,
        responses: list[str] | None = None,
        default_response: str = "Mock response",
        simulate_latency_ms: int = 0,
    ) -> None:
        """
        Initialize mock adapter.

        Args:
            responses: Queue of responses (FIFO). If exhausted, uses default_response.
            default_response: Fallback response
            simulate_latency_ms: Artificial delay to simulate API latency
        """
        self._responses = list(responses) if responses else []
        self._default_response = default_response
        self._simulate_latency_ms = simulate_latency_ms
        self._call_count = 0
        self._cached_prefixes: set[str] = set()
        self.last_messages: list[Any] = []
        self.last_kwargs: dict[str, Any] = {}

    async def invoke(
        self,
        messages: list[Message] | list[Any],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Return predefined response with simulated prompt cache accounting."""
        if self._simulate_latency_ms > 0:
            await asyncio.sleep(self._simulate_latency_ms / 1000)

        self._call_count += 1
        self.last_messages = list(messages)
        self.last_kwargs = dict(kwargs)

        if self._responses:
            content = self._responses.pop(0)
        else:
            content = self._default_response

        input_tokens = 0
        cache_read_tokens = 0
        cache_creation_tokens = 0

        for msg in messages:
            if isinstance(msg, Message):
                text = msg.content
                wants_cache = bool(msg.cache_control)
            elif isinstance(msg, dict):
                text = str(msg.get("content", ""))
                wants_cache = bool(msg.get("cache_control"))
            else:
                text = str(msg)
                wants_cache = False

            tok_count = len(text.split())
            if wants_cache and tok_count > 0:
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
                if digest in self._cached_prefixes:
                    cache_read_tokens += tok_count
                else:
                    self._cached_prefixes.add(digest)
                    cache_creation_tokens += tok_count
            input_tokens += tok_count

        output_tokens = len(content.split())

        return ModelResponse(
            content=content,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
            ),
            finish_reason="stop",
            model="mock-model",
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelChunk]:
        """Stream response word by word."""
        response = await self.invoke(messages, tools, temperature, max_tokens, **kwargs)

        words = response.content.split()
        for i, word in enumerate(words):
            yield ModelChunk(
                content=word + " ",
                finish_reason="stop" if i == len(words) - 1 else None,
            )
            if self._simulate_latency_ms > 0:
                await asyncio.sleep(self._simulate_latency_ms / 1000)
