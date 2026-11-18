"""
Model adapter protocol and shared types.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class Role(str, Enum):
    """Message role."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    """Conversation message."""

    role: Role
    content: str
    name: str | None = None  # For tool messages


@dataclass
class Tool:
    """Tool definition for function calling."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass
class ToolCall:
    """Tool invocation request from model."""

    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class Usage:
    """Token usage statistics."""

    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass
class ModelResponse:
    """Response from model invocation."""

    content: str
    tool_calls: list[ToolCall] | None = None
    usage: Usage | None = None
    finish_reason: str | None = None  # "stop", "length", "tool_calls"
    model: str | None = None


@dataclass
class ModelChunk:
    """Streaming response chunk."""

    content: str
    finish_reason: str | None = None


@runtime_checkable
class ModelAdapter(Protocol):
    """
    Protocol for LLM provider adapters.

    All methods are async. Adapters MUST handle:
    - Rate limiting (exponential backoff)
    - Retries for transient failures
    - Token counting
    - Error translation
    """

    async def invoke(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """
        Invoke model with messages.

        Args:
            messages: Conversation history
            tools: Available tools for function calling
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            **kwargs: Provider-specific parameters

        Returns:
            ModelResponse with content, tool_calls, usage

        Raises:
            RateLimitError: Rate limit exceeded (retry after delay)
            ModelError: Permanent failure (invalid request, etc.)
        """
        ...

    def stream(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelChunk]:
        """
        Stream model response.

        Yields:
            ModelChunk objects until finish_reason set
        """
        ...


class RateLimitError(Exception):
    """Rate limit exceeded, retry after delay."""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Rate limit exceeded, retry after {retry_after_seconds}s")


class ModelError(Exception):
    """Permanent model error (invalid request, unavailable, etc.)."""
