"""
Anthropic Claude model adapter implementation.
"""

import asyncio
import json
import logging
import os
import random
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx

from product_value_system.adapters.model_adapter import (
    Message,
    ModelAdapter,
    ModelChunk,
    ModelError,
    ModelResponse,
    ModelUsage,
    NetworkError,
    RateLimitError,
    Role,
    Tool,
    ToolCall,
)
from product_value_system.security.secret_detection import SecretDetector

if TYPE_CHECKING:
    from product_value_system.caching.prompt_cache import PromptCache

logger = logging.getLogger(__name__)


class AnthropicAdapter:
    """
    Model adapter for Anthropic Claude models.

    Implements the ModelAdapter protocol for compatibility with
    AI-Native Product Value System agents.

    Supported Models:
    - claude-3-5-sonnet-20241022 (recommended)
    - claude-3-5-sonnet-latest
    - claude-3-opus-20240229
    - claude-3-sonnet-20240229
    - claude-3-haiku-20240307

    Authentication:
    - Via ANTHROPIC_API_KEY environment variable
    - Or pass api_key parameter to constructor

    Example:
        adapter = AnthropicAdapter(api_key="sk-ant-...")
        response = await adapter.invoke([
            {"role": "user", "content": "Hello"}
        ])
        print(response.content)
    """

    # Pricing in USD per 1M tokens (as of 2024-10)
    PRICING: dict[str, dict[str, float]] = {
        "claude-3-5-sonnet-20241022": {
            "input": 3.00,
            "output": 15.00,
        },
        "claude-3-5-sonnet-latest": {
            "input": 3.00,
            "output": 15.00,
        },
        "claude-3-sonnet-20240229": {
            "input": 3.00,
            "output": 15.00,
        },
        "claude-3-opus-20240229": {
            "input": 15.00,
            "output": 75.00,
        },
        "claude-3-haiku-20240307": {
            "input": 0.25,
            "output": 1.25,
        },
    }

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        cache: "PromptCache | None" = None,
        cache_max_temperature: float = 0.3,
    ) -> None:
        """
        Initialize Anthropic adapter.

        Args:
            api_key: Anthropic API key (or use ANTHROPIC_API_KEY env var)
            base_url: API base URL (defaults to ANTHROPIC_BASE_URL or https://api.anthropic.com)
            model: Default model name (defaults to ANTHROPIC_DEFAULT_MODEL or Sonnet 3.5)
            timeout: Request timeout in seconds
            max_retries: Max retry attempts on transient errors
            transport: Optional custom httpx transport (e.g. httpx.MockTransport for testing)
            client: Optional pre-configured httpx.AsyncClient
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Anthropic API key required. Set ANTHROPIC_API_KEY env var "
                "or pass api_key parameter."
            )
        if not self.api_key.startswith("sk-ant-"):
            logger.warning(
                "Anthropic API key does not start with expected 'sk-ant-' prefix"
            )

        self._secret_detector = SecretDetector()

        resolved_base_url = (
            base_url
            or os.getenv("ANTHROPIC_BASE_URL")
            or "https://api.anthropic.com"
        )
        self.base_url = resolved_base_url.rstrip("/")
        self.model = (
            model
            or os.getenv("ANTHROPIC_DEFAULT_MODEL")
            or "claude-3-5-sonnet-20241022"
        )
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache = cache
        self.cache_max_temperature = float(cache_max_temperature)

        if client is not None:
            self.client = client
        else:
            client_kwargs: dict[str, Any] = {
                "base_url": self.base_url,
                "headers": {
                    "Authorization": f"Bearer {self.api_key}",
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                "timeout": httpx.Timeout(timeout=self.timeout),
            }
            if transport is not None:
                client_kwargs["transport"] = transport
            self.client = httpx.AsyncClient(**client_kwargs)

    def _build_payload(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[Tool | dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = 2000,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Validate messages and build Anthropic /v1/messages JSON payload."""
        if not messages:
            raise ValueError("messages list cannot be empty")

        system_messages: list[str] = []
        user_messages: list[dict[str, Any]] = []

        for msg in messages:
            if isinstance(msg, Message):
                role_str = msg.role.value
                content_str = msg.content
            elif isinstance(msg, dict):
                role_val = msg.get("role")
                role_str = role_val.value if isinstance(role_val, Role) else str(role_val or "")
                content_str = str(msg.get("content", ""))
            else:
                raise ValueError(f"Unsupported message type: {type(msg)}")

            if role_str not in {"system", "user", "assistant", "tool"}:
                raise ValueError(f"Invalid message role: '{role_str}'")

            if role_str == "system":
                system_messages.append(content_str)
            else:
                mapped_role = "user" if role_str == "tool" else role_str
                user_messages.append({"role": mapped_role, "content": content_str})

        resolved_max_tokens = max_tokens if max_tokens is not None else 2000
        payload: dict[str, Any] = {
            "model": kwargs.pop("model", self.model),
            "messages": user_messages,
            "max_tokens": resolved_max_tokens,
            "temperature": temperature,
        }

        if system_messages:
            payload["system"] = "\n".join(system_messages)

        if tools:
            payload["tools"] = self._convert_tools(tools)

        payload.update(kwargs)
        return payload

    async def invoke(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[Tool | dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = 2000,
        **kwargs: Any,
    ) -> ModelResponse:
        """
        Invoke Claude model with messages.

        Args:
            messages: List of Message dataclasses or dicts with 'role' and 'content'
                      Role can be 'user', 'assistant', 'system', or 'tool'
            tools: Optional list of Tool dataclasses or OpenAI/Anthropic tool definitions
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate (default: 2000)
            **kwargs: Additional API parameters (e.g. model override)

        Returns:
            ModelResponse with content, finish_reason, usage, tool_calls

        Raises:
            ValueError: If messages list is empty or contains invalid roles
            ModelError: API error or malformed response
            RateLimitError: 429 rate limit exceeded
            NetworkError: Connection/timeout issues
        """
        payload = self._build_payload(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        model_name = str(payload.get("model") or self.model)
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
                logger.info("PromptCache hit for Anthropic model %s", model_name)
                return deserialize_model_response(cached)

        for attempt in range(self.max_retries):
            try:
                response = await self.client.post(
                    "/v1/messages",
                    json=payload,
                )

                if response.status_code == 429:
                    retry_after_raw = response.headers.get("Retry-After", "60")
                    try:
                        retry_after = float(retry_after_raw)
                    except ValueError:
                        retry_after = 60.0

                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(retry_after)
                        continue
                    raise RateLimitError(
                        f"Rate limit exceeded. Retry after {retry_after}s",
                        retry_after_seconds=retry_after,
                    )

                if response.status_code >= 400:
                    try:
                        error_data = response.json()
                        error_msg = (
                            error_data.get("error", {}).get("message")
                            if isinstance(error_data, dict)
                            else str(error_data)
                        ) or f"HTTP {response.status_code}"
                    except (json.JSONDecodeError, ValueError):
                        error_msg = response.text or f"HTTP {response.status_code}"
                    safe_msg = self._secret_detector.redact_secrets(str(error_msg))
                    raise ModelError(f"Anthropic API error: {safe_msg}")

                data: dict[str, Any] = response.json()
                model_response = self._parse_response(data)
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
                    backoff = (2**attempt) + random.uniform(0.0, 0.25)
                    await asyncio.sleep(backoff)
                    continue
                raise NetworkError(
                    f"Request failed after {self.max_retries} attempts: {exc}"
                ) from exc

        raise ModelError("Unexpected: retry loop exhausted")

    async def stream(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[Tool | dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = 2000,
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
            ModelError: API error or SSE error event
            NetworkError: Connection dropped before or during streaming
        """
        payload = self._build_payload(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        payload["stream"] = True

        input_tokens = 0
        output_tokens = 0
        finish_reason: str | None = None
        yielded_any = False
        yielded_final = False

        finish_reason_map: dict[str | None, str | None] = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
        }

        for attempt in range(self.max_retries):
            try:
                async with self.client.stream("POST", "/v1/messages", json=payload) as response:
                    if response.status_code == 429:
                        retry_after_raw = response.headers.get("Retry-After", "60")
                        try:
                            retry_after = float(retry_after_raw)
                        except ValueError:
                            retry_after = 60.0
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(retry_after)
                            continue
                        raise RateLimitError(
                            f"Rate limit exceeded. Retry after {retry_after}s",
                            retry_after_seconds=retry_after,
                        )

                    if response.status_code >= 400:
                        error_bytes = await response.aread()
                        safe_error = self._secret_detector.redact_secrets(
                            error_bytes.decode("utf-8", errors="replace")
                        )
                        raise ModelError(f"Anthropic API error: {safe_error}")

                    current_event: str | None = None
                    async for raw_line in response.aiter_lines():
                        line = raw_line.strip()
                        if not line or line.startswith(":"):
                            continue

                        if line.startswith("event: "):
                            current_event = line[7:].strip()
                            continue

                        if not line.startswith("data: "):
                            continue

                        data_str = line[6:]
                        try:
                            data: dict[str, Any] = json.loads(data_str)
                        except json.JSONDecodeError:
                            logger.warning("Skipping malformed Anthropic SSE JSON payload")
                            continue

                        event_type = current_event or str(data.get("type", ""))

                        if event_type == "message_start":
                            msg_usage = data.get("message", {}).get("usage", {})
                            if isinstance(msg_usage, dict) and "input_tokens" in msg_usage:
                                input_tokens = int(msg_usage["input_tokens"])

                        elif event_type == "content_block_delta":
                            delta_data = data.get("delta", {})
                            if delta_data.get("type") == "text_delta":
                                content_delta = str(delta_data.get("text", ""))
                                if content_delta:
                                    yielded_any = True
                                    yield ModelChunk(
                                        delta=content_delta,
                                        finish_reason=None,
                                        usage=None,
                                    )

                        elif event_type == "message_delta":
                            delta_data = data.get("delta", {})
                            stop_reason = delta_data.get("stop_reason")
                            if stop_reason is not None:
                                finish_reason = finish_reason_map.get(stop_reason, str(stop_reason))

                            usage_data = data.get("usage", {})
                            if isinstance(usage_data, dict):
                                if "input_tokens" in usage_data:
                                    input_tokens = int(usage_data["input_tokens"])
                                if "output_tokens" in usage_data:
                                    output_tokens = int(usage_data["output_tokens"])

                        elif event_type == "error":
                            err = data.get("error", {})
                            err_msg = (
                                err.get("message", str(err)) if isinstance(err, dict) else str(err)
                            )
                            safe_err = self._secret_detector.redact_secrets(err_msg)
                            raise ModelError(f"Anthropic stream error: {safe_err}")

                        elif event_type == "message_stop":
                            yielded_any = True
                            yielded_final = True
                            accumulated_usage = ModelUsage(
                                prompt_tokens=input_tokens,
                                completion_tokens=output_tokens,
                                total_tokens=input_tokens + output_tokens,
                            )
                            yield ModelChunk(
                                delta="",
                                finish_reason=finish_reason or "stop",
                                usage=accumulated_usage,
                            )
                            break

                if not yielded_final and (yielded_any or finish_reason is not None):
                    yield ModelChunk(
                        delta="",
                        finish_reason=finish_reason or "stop",
                        usage=ModelUsage(
                            prompt_tokens=input_tokens,
                            completion_tokens=output_tokens,
                            total_tokens=input_tokens + output_tokens,
                        ),
                    )
                return

            except httpx.RequestError as exc:
                if not yielded_any and attempt < self.max_retries - 1:
                    backoff = (2**attempt) + random.uniform(0.0, 0.25)
                    await asyncio.sleep(backoff)
                    continue
                raise NetworkError(f"Stream connection failed: {exc}") from exc

    def _parse_response(self, data: dict[str, Any]) -> ModelResponse:
        """Parse Anthropic API response into ModelResponse."""
        content_blocks = data.get("content", [])
        text_content = ""
        tool_calls: list[ToolCall] = []

        for block in content_blocks:
            block_type = block.get("type")
            if block_type == "text":
                text_content += str(block.get("text", ""))
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=str(block.get("id", "")),
                        name=str(block.get("name", "")),
                        arguments=block.get("input", {}),
                    )
                )

        stop_reason = data.get("stop_reason")
        finish_reason_map: dict[str | None, str | None] = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
        }
        finish_reason = finish_reason_map.get(stop_reason, stop_reason)

        usage_data = data.get("usage", {})
        input_tokens = int(usage_data.get("input_tokens", 0))
        output_tokens = int(usage_data.get("output_tokens", 0))
        usage = ModelUsage(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )

        # Sanitize response content for accidentally leaked secrets/keys
        sanitized_content = self._secret_detector.redact_secrets(text_content)

        resolved_model = str(data.get("model", self.model))
        cost_usd = self.calculate_cost(usage, resolved_model)
        logger.debug(
            "Anthropic invocation complete: model=%s input=%d output=%d cost_usd=%.6f",
            resolved_model,
            input_tokens,
            output_tokens,
            cost_usd,
        )

        return ModelResponse(
            content=sanitized_content,
            finish_reason=finish_reason,
            usage=usage,
            tool_calls=tool_calls if tool_calls else None,
            model=resolved_model,
        )

    def _convert_tools(self, tools: list[Tool | dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Convert Tool dataclasses or OpenAI-style tool definitions to Anthropic format.

        OpenAI format:
        {
          "type": "function",
          "function": {
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {...}
          }
        }

        Anthropic format:
        {
          "name": "get_weather",
          "description": "Get weather",
          "input_schema": {...}
        }
        """
        anthropic_tools: list[dict[str, Any]] = []
        for tool in tools:
            if isinstance(tool, Tool):
                anthropic_tools.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.parameters,
                    }
                )
            elif isinstance(tool, dict):
                if tool.get("type") == "function" and "function" in tool:
                    func = tool["function"]
                    anthropic_tools.append(
                        {
                            "name": func["name"],
                            "description": func.get("description", ""),
                            "input_schema": func.get("parameters", {}),
                        }
                    )
                elif "name" in tool and "input_schema" in tool:
                    anthropic_tools.append(tool)
                elif "name" in tool and "parameters" in tool:
                    anthropic_tools.append(
                        {
                            "name": tool["name"],
                            "description": tool.get("description", ""),
                            "input_schema": tool.get("parameters", {}),
                        }
                    )
        return anthropic_tools

    def calculate_cost(self, usage: ModelUsage, model: str | None = None) -> float:
        """
        Calculate cost in USD from token usage.

        Args:
            usage: Token usage stats
            model: Model name (defaults to self.model)

        Returns:
            Cost in USD
        """
        resolved_model = model or self.model
        pricing = self.PRICING.get(
            resolved_model,
            self.PRICING["claude-3-5-sonnet-20241022"],
        )

        input_cost = (usage.prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (usage.completion_tokens / 1_000_000) * pricing["output"]

        return input_cost + output_cost

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()


AnthropicModelAdapter = AnthropicAdapter

__all__ = [
    "AnthropicAdapter",
    "AnthropicModelAdapter",
    "ModelAdapter",
    "ModelChunk",
    "ModelError",
    "ModelResponse",
    "ModelUsage",
    "NetworkError",
    "RateLimitError",
]
