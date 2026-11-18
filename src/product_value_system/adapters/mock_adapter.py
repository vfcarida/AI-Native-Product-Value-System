"""
Mock model adapter for testing without API calls.
"""

import asyncio
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

    async def invoke(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Return predefined response."""
        if self._simulate_latency_ms > 0:
            await asyncio.sleep(self._simulate_latency_ms / 1000)

        self._call_count += 1

        if self._responses:
            content = self._responses.pop(0)
        else:
            content = self._default_response

        input_tokens = sum(len(msg.content.split()) for msg in messages)
        output_tokens = len(content.split())

        return ModelResponse(
            content=content,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
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
