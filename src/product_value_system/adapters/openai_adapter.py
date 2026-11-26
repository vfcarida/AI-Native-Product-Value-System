"""
OpenAI model adapter implementation.
"""

import asyncio
import json
import logging
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
from product_value_system.secrets.vault_client import resolve_secret
from product_value_system.security.secret_detection import SecretDetector

if TYPE_CHECKING:
    from product_value_system.caching.prompt_cache import PromptCache

logger = logging.getLogger(__name__)


class OpenAIModelAdapter:
    """
    Adapter for OpenAI API (gpt-4, gpt-4-turbo, gpt-3.5-turbo, etc.).

    Handles:
    - Rate limiting with exponential backoff
    - Retries for transient failures
    - Real Server-Sent Events (SSE) streaming
    - Tool/function calling
    - Token usage tracking and USD cost calculation
    """

    # Pricing in USD per 1M tokens
    PRICING: dict[str, dict[str, float]] = {
        "gpt-4": {"input": 30.00, "output": 60.00},
        "gpt-4-turbo": {"input": 10.00, "output": 30.00},
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    }

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        cache: "PromptCache | None" = None,
        cache_max_temperature: float = 0.3,
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
        self.api_key = resolve_secret(
            vault_path="product-value-system/openai",
            key="api_key",
            env_vars=("OPENAI_API_KEY",),
            explicit=api_key,
        )
        if not self.api_key:
            raise ValueError(
                "OpenAI API key required (configure HashiCorp Vault or set OPENAI_API_KEY)"
            )

        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache = cache
        self.cache_max_temperature = float(cache_max_temperature)
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
        """Invoke OpenAI chat completion API (with optional deterministic token cache)."""
        model_name = str(kwargs.get("model") or self.model)
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
                logger.info("PromptCache hit for OpenAI model %s", model_name)
                return deserialize_model_response(cached)

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
        """Build OpenAI API request payload with native Structured Outputs & prompt caching."""
        import hashlib

        response_schema = kwargs.pop("response_schema", None)
        prompt_cache_key = kwargs.pop("prompt_cache_key", None)

        formatted_messages: list[dict[str, Any]] = []
        cached_segments: list[str] = []
        for msg in messages:
            if isinstance(msg, Message):
                formatted_messages.append({"role": msg.role.value, "content": msg.content})
                if msg.cache_control:
                    cached_segments.append(f"{msg.role.value}:{msg.content}")
            elif isinstance(msg, dict):
                role_val = msg.get("role", "user")
                role_str = role_val.value if isinstance(role_val, Role) else str(role_val)
                content_str = str(msg.get("content", ""))
                formatted_messages.append({"role": role_str, "content": content_str})
                if msg.get("cache_control"):
                    cached_segments.append(f"{role_str}:{content_str}")
            else:
                raise ValueError(f"Unsupported message type: {type(msg)}")

        payload: dict[str, Any] = {
            "model": kwargs.pop("model", self.model),
            "messages": formatted_messages,
            "temperature": temperature,
        }

        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        if response_schema is not None and "response_format" not in kwargs:
            schema_name, json_schema = self._normalize_response_schema(response_schema)
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": json_schema,
                },
            }

        if prompt_cache_key:
            payload["prompt_cache_key"] = str(prompt_cache_key)
        elif cached_segments:
            prefix_hash = hashlib.sha256("\n".join(cached_segments).encode("utf-8")).hexdigest()
            payload["prompt_cache_key"] = f"pvs-cache-{prefix_hash[:16]}"

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

    @staticmethod
    def _normalize_response_schema(schema_or_model: Any) -> tuple[str, dict[str, Any]]:
        """Convert Pydantic BaseModel subclass or JSON Schema dict to OpenAI strict schema."""
        if isinstance(schema_or_model, type) and hasattr(schema_or_model, "model_json_schema"):
            raw = dict(schema_or_model.model_json_schema())
            raw.setdefault("additionalProperties", False)
            return schema_or_model.__name__, raw
        if isinstance(schema_or_model, dict):
            raw = dict(schema_or_model)
            raw.setdefault("additionalProperties", False)
            return str(raw.get("title", "StructuredResponse")), raw
        raise ValueError(f"Unsupported response_schema type: {type(schema_or_model)}")

    def _parse_response(self, data: dict[str, Any]) -> ModelResponse:
        """Parse OpenAI API response including native cached token usage."""
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
            prompt_details = u.get("prompt_tokens_details") or {}
            cached_tokens = int(
                prompt_details.get("cached_tokens", 0)
                if isinstance(prompt_details, dict)
                else 0
            )
            usage = Usage(
                input_tokens=u["prompt_tokens"],
                output_tokens=u["completion_tokens"],
                total_tokens=u["total_tokens"],
                cache_read_tokens=cached_tokens,
            )

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            model=data.get("model"),
        )

    def calculate_cost(self, usage: Usage, model: str | None = None) -> float:
        """
        Calculate cost in USD from token usage, including 50% OpenAI prompt cache discount.

        Args:
            usage: Token usage stats
            model: Model identifier (defaults to self.model)

        Returns:
            Cost in USD
        """
        resolved_model = model or self.model
        pricing = self.PRICING.get(resolved_model, self.PRICING["gpt-4"])

        cached_tokens = min(usage.cache_read_tokens, usage.prompt_tokens)
        uncached_prompt_tokens = max(0, usage.prompt_tokens - cached_tokens)
        input_cost = (uncached_prompt_tokens / 1_000_000) * pricing["input"]
        cached_cost = (cached_tokens / 1_000_000) * (pricing["input"] * 0.50)
        output_cost = (usage.completion_tokens / 1_000_000) * pricing["output"]
        return input_cost + cached_cost + output_cost

    async def close(self) -> None:
        """Close underlying HTTP client."""
        await self._client.aclose()


OpenAIAdapter = OpenAIModelAdapter
