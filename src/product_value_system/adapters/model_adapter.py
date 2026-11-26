"""
Model adapter protocol, shared types, Structured Outputs helpers, and native
Prompt Caching token accounting (EVO-P3-08).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

TModel = TypeVar("TModel", bound=BaseModel)


class Role(str, Enum):
    """Message role."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    """Conversation message with optional native prompt caching control."""

    role: Role
    content: str
    name: str | None = None  # For tool messages
    cache_control: bool = False


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
    """
    Token usage statistics supporting input/output, prompt/completion naming,
    and native LLM provider prompt caching metrics.
    """

    def __init__(
        self,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
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
        self.cache_read_tokens: int = max(0, int(cache_read_tokens))
        self.cache_creation_tokens: int = max(0, int(cache_creation_tokens))

    @property
    def prompt_tokens(self) -> int:
        """Alias for input_tokens."""
        return self.input_tokens

    @property
    def completion_tokens(self) -> int:
        """Alias for output_tokens."""
        return self.output_tokens

    @property
    def cache_hit_ratio(self) -> float:
        """Fraction of prompt tokens served from provider prompt cache (0.0..1.0)."""
        if self.cache_read_tokens <= 0:
            return 0.0
        if self.total_tokens >= self.input_tokens + self.output_tokens + self.cache_read_tokens:
            prompt_total = (
                self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens
            )
        else:
            prompt_total = max(self.input_tokens, self.cache_read_tokens)
        if prompt_total <= 0:
            return 0.0
        return min(1.0, self.cache_read_tokens / prompt_total)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Usage):
            return NotImplemented
        return (
            self.input_tokens == other.input_tokens
            and self.output_tokens == other.output_tokens
            and self.total_tokens == other.total_tokens
            and self.cache_read_tokens == other.cache_read_tokens
            and self.cache_creation_tokens == other.cache_creation_tokens
        )

    def __repr__(self) -> str:
        base = (
            f"Usage(input_tokens={self.input_tokens}, "
            f"output_tokens={self.output_tokens}, "
            f"total_tokens={self.total_tokens}"
        )
        if self.cache_read_tokens or self.cache_creation_tokens:
            base += (
                f", cache_read_tokens={self.cache_read_tokens}, "
                f"cache_creation_tokens={self.cache_creation_tokens}"
            )
        return base + ")"


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


class StructuredOutputError(ModelError):
    """Raised when model output cannot be parsed or validated against a target schema."""

    def __init__(self, message: str, *, raw_output: str = "", attempts: int = 1) -> None:
        super().__init__(message)
        self.raw_output = raw_output
        self.attempts = attempts


def schema_to_json_schema(schema_or_model: type[BaseModel] | dict[str, Any]) -> dict[str, Any]:
    """Return a JSON Schema dictionary from a Pydantic BaseModel subclass or dict."""
    if isinstance(schema_or_model, type) and issubclass(schema_or_model, BaseModel):
        return dict(schema_or_model.model_json_schema())
    if isinstance(schema_or_model, dict):
        return dict(schema_or_model)
    raise TypeError(f"Expected Pydantic BaseModel subclass or dict, got {type(schema_or_model)}")


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_json_payload(raw_text: str) -> str:
    """Extract raw JSON object/array text from a model response (stripping markdown fences)."""
    text = raw_text.strip()
    if not text:
        return text

    fence_match = _JSON_FENCE_RE.search(text)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate:
            return candidate

    if text[0] in "{[":
        return text

    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end > start:
            return text[start : end + 1].strip()
    return text


def parse_structured_output(raw_text: str, schema: type[TModel]) -> TModel:
    """Parse and validate raw model output against a Pydantic ``BaseModel`` schema."""
    payload = extract_json_payload(raw_text)
    parsed_obj = json.loads(payload)
    return schema.model_validate(parsed_obj)
