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
    """Tool invocation request from model.

    Supports both attribute access (``tc.name``) and dictionary subscript
    access (``tc["name"]``) for compatibility across OpenAI and Anthropic
    tool call conventions.
    """

    id: str
    name: str
    arguments: Any  # JSON string or parsed dict

    def __getitem__(self, key: str) -> Any:
        """Allow dict-style subscript access to ToolCall fields."""
        if key in {"id", "name", "arguments"}:
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        """Return field value or default."""
        if key in {"id", "name", "arguments"}:
            return getattr(self, key)
        return default


class Usage:
    """Token usage statistics supporting both input/output and prompt/completion naming."""

    def __init__(
        self,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        resolved_input = (
            input_tokens
            if input_tokens is not None
            else (prompt_tokens if prompt_tokens is not None else 0)
        )
        resolved_output = (
            output_tokens
            if output_tokens is not None
            else (completion_tokens if completion_tokens is not None else 0)
        )
        self.input_tokens: int = resolved_input
        self.output_tokens: int = resolved_output
        self.total_tokens: int = (
            total_tokens if total_tokens is not None else resolved_input + resolved_output
        )

    @property
    def prompt_tokens(self) -> int:
        """Alias for input_tokens."""
        return self.input_tokens

    @property
    def completion_tokens(self) -> int:
        """Alias for output_tokens."""
        return self.output_tokens

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Usage):
            return NotImplemented
        return (
            self.input_tokens == other.input_tokens
            and self.output_tokens == other.output_tokens
            and self.total_tokens == other.total_tokens
        )

    def __repr__(self) -> str:
        return (
            f"Usage(input_tokens={self.input_tokens}, "
            f"output_tokens={self.output_tokens}, "
            f"total_tokens={self.total_tokens})"
        )


ModelUsage = Usage


@dataclass
class ModelResponse:
    """Response from model invocation."""

    content: str
    tool_calls: list[Any] | None = None
    usage: Usage | None = None
    finish_reason: str | None = None  # "stop", "length", "tool_calls"
    model: str | None = None


class ModelChunk:
    """Streaming response chunk supporting both content and delta attributes."""

    def __init__(
        self,
        content: str | None = None,
        finish_reason: str | None = None,
        usage: Usage | None = None,
        *,
        delta: str | None = None,
    ) -> None:
        resolved_text = content if content is not None else (delta if delta is not None else "")
        self.content: str = resolved_text
        self.finish_reason: str | None = finish_reason
        self.usage: Usage | None = usage

    @property
    def delta(self) -> str:
        """Alias for content."""
        return self.content

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ModelChunk):
            return NotImplemented
        return (
            self.content == other.content
            and self.finish_reason == other.finish_reason
            and self.usage == other.usage
        )

    def __repr__(self) -> str:
        return (
            f"ModelChunk(content={self.content!r}, "
            f"finish_reason={self.finish_reason!r}, "
            f"usage={self.usage!r})"
        )


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
        messages: list[Any],
        tools: list[Any] | None = None,
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
        messages: list[Any],
        tools: list[Any] | None = None,
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


class ModelError(Exception):
    """Permanent model error (invalid request, unavailable, etc.)."""


class RateLimitError(ModelError):
    """Rate limit exceeded, retry after delay."""

    def __init__(
        self,
        message_or_retry_after: float | int | str = 60.0,
        retry_after_seconds: float | None = None,
    ) -> None:
        if isinstance(message_or_retry_after, (int, float)):
            self.retry_after_seconds = float(message_or_retry_after)
            super().__init__(f"Rate limit exceeded, retry after {self.retry_after_seconds}s")
        else:
            self.retry_after_seconds = (
                float(retry_after_seconds) if retry_after_seconds is not None else 60.0
            )
            super().__init__(message_or_retry_after)


class NetworkError(ModelError):
    """Network connectivity or request timeout error."""
