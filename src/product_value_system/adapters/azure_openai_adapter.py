"""
Native Azure OpenAI Service model adapter implementation (v1.0).

Speaks the Azure OpenAI deployment REST API directly over ``httpx``:
``{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=...``

Supports:
- API-key (``api-key`` header) and Microsoft Entra ID bearer token authentication
- Deployment-scoped routing & configurable ``api-version``
- Tool/function calling & real Server-Sent Events (SSE) streaming with usage chunks
- Rate-limit (HTTP 429 ``Retry-After``) & transient retry handling with secret redaction
- Optional deterministic ``PromptCache`` integration and USD cost calculation
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
from product_value_system.secrets.vault_client import resolve_secret
from product_value_system.security.secret_detection import SecretDetector

if TYPE_CHECKING:
    from product_value_system.caching.prompt_cache import PromptCache

logger = logging.getLogger(__name__)


class AzureOpenAIModelAdapter:
    """Native adapter for Azure OpenAI Service deployments."""

    PRICING: dict[str, dict[str, float]] = {
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gpt-4-turbo": {"input": 10.00, "output": 30.00},
        "gpt-4": {"input": 30.00, "output": 60.00},
        "gpt-35-turbo": {"input": 0.50, "output": 1.50},
        "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    }

    def __init__(
        self,
        endpoint: str | None = None,
        deployment: str | None = None,
        api_key: str | None = None,
        *,
        azure_ad_token: str | None = None,
        api_version: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        cache: PromptCache | None = None,
        cache_max_temperature: float = 0.3,
    ) -> None:
        resolved_endpoint = (endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT") or "").strip()
        if not resolved_endpoint and client is None:
            raise ValueError(
                "Azure OpenAI endpoint required (set AZURE_OPENAI_ENDPOINT env var)"
            )
        if resolved_endpoint and not resolved_endpoint.startswith(("https://", "http://")):
            raise ValueError(
                f"Azure OpenAI endpoint must start with https:// or http://, got "
                f"{resolved_endpoint!r}"
            )

        resolved_deployment = (
            deployment or model or os.environ.get("AZURE_OPENAI_DEPLOYMENT") or ""
        ).strip()
        if not resolved_deployment:
            raise ValueError(
                "Azure OpenAI deployment name required (pass deployment= or set "
                "AZURE_OPENAI_DEPLOYMENT)"
            )

        resolved_key = resolve_secret(
            vault_path="product-value-system/azure_openai",
            key="api_key",
            env_vars=("AZURE_OPENAI_API_KEY",),
            explicit=api_key,
        )
        resolved_token = azure_ad_token or os.environ.get("AZURE_OPENAI_AD_TOKEN")
        if not resolved_key and not resolved_token and client is None:
            raise ValueError(
                "Azure OpenAI credentials required (set AZURE_OPENAI_API_KEY or "
                "AZURE_OPENAI_AD_TOKEN)"
            )

        self.endpoint = resolved_endpoint.rstrip("/")
        self.deployment = resolved_deployment
        self.model = model or resolved_deployment
        self.api_version = (
            api_version or os.environ.get("AZURE_OPENAI_API_VERSION") or "2024-10-21"
        )
        self.api_key = resolved_key or ""
        self.azure_ad_token = resolved_token
        self.timeout = float(timeout)
        self.max_retries = max(1, int(max_retries))
        self.cache = cache
        self.cache_max_temperature = float(cache_max_temperature)
        self._secret_detector = SecretDetector()

        if client is not None:
            self._client = client
        else:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self.azure_ad_token:
                headers["Authorization"] = f"Bearer {self.azure_ad_token}"
            else:
                headers["api-key"] = self.api_key

            client_kwargs: dict[str, Any] = {
                "base_url": self.endpoint,
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

    def _chat_path(self, deployment: str | None = None) -> str:
        dep = deployment or self.deployment
        return f"/openai/deployments/{dep}/chat/completions"

    async def invoke(
        self,
        messages: list[Message] | list[dict[str, Any]],
        tools: list[Tool] | list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Invoke Azure OpenAI Chat Completions deployment API."""
        deployment_name = str(kwargs.pop("deployment", None) or self.deployment)
        model_name = str(kwargs.pop("model", None) or self.model)
        should_cache = self.cache is not None and temperature < self.cache_max_temperature
        if should_cache and self.cache is not None:
            from product_value_system.caching.prompt_cache import deserialize_model_response

            cached = await self.cache.get(
                messages=messages,
                model=f"azure:{deployment_name}",
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
            )
            if cached is not None:
                logger.info("PromptCache hit for Azure OpenAI deployment %s", deployment_name)
                return deserialize_model_response(cached)

        payload = self._build_payload(messages, tools, temperature, max_tokens, **kwargs)
        path = self._chat_path(deployment_name)

        for attempt in range(self.max_retries):
            try:
                response = await self._client.post(
                    path,
                    params={"api-version": self.api_version},
                    json=payload,
                )

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
                    raise ModelError(f"Azure OpenAI API error: {response.status_code} {safe_text}")

                data: dict[str, Any] = response.json()
                model_response = self._parse_response(data, fallback_model=model_name)

                if should_cache and self.cache is not None:
                    from product_value_system.caching.prompt_cache import serialize_model_response

                    await self.cache.set(
                        messages=messages,
                        model=f"azure:{deployment_name}",
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
                raise NetworkError(f"Azure OpenAI request failed: {exc}") from exc

        raise ModelError("Max retries exceeded for Azure OpenAI request")

    async def stream(
        self,
        messages: list[Message] | list[dict[str, Any]],
        tools: list[Tool] | list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelChunk]:
        """Stream Azure OpenAI SSE response chunks."""
        deployment_name = str(kwargs.pop("deployment", None) or self.deployment)
        kwargs.pop("model", None)
        payload = self._build_payload(messages, tools, temperature, max_tokens, **kwargs)
        payload["stream"] = True
        payload.setdefault("stream_options", {"include_usage": True})
        path = self._chat_path(deployment_name)

        for attempt in range(self.max_retries):
            yielded_any = False
            yielded_final = False
            accumulated_usage: ModelUsage | None = None
            finish_reason: str | None = None

            try:
                async with self._client.stream(
                    "POST",
                    path,
                    params={"api-version": self.api_version},
                    json=payload,
                ) as response:
                    if response.status_code == 429:
                        retry_after = float(response.headers.get("Retry-After", 5))
                        if not yielded_any and attempt < self.max_retries - 1:
                            await asyncio.sleep(retry_after)
                            continue
                        raise RateLimitError(retry_after)

                    if response.status_code >= 400:
                        raw_err = (await response.aread()).decode("utf-8", errors="replace")
                        safe_err = self._secret_detector.redact_secrets(raw_err)
                        raise ModelError(
                            f"Azure OpenAI stream error: {response.status_code} {safe_err}"
                        )

                    async for raw_line in response.aiter_lines():
                        line = raw_line.strip()
                        if not line or line.startswith(":") or not line.startswith("data:"):
                            continue

                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break

                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        usage_data = data.get("usage")
                        if isinstance(usage_data, dict):
                            p_tok = int(usage_data.get("prompt_tokens", 0))
                            c_tok = int(usage_data.get("completion_tokens", 0))
                            t_tok = int(usage_data.get("total_tokens", p_tok + c_tok))
                            accumulated_usage = ModelUsage(
                                prompt_tokens=p_tok,
                                completion_tokens=c_tok,
                                total_tokens=t_tok,
                            )

                        choices = data.get("choices") or []
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
                        delta = choice.get("delta") or {}
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
                    await asyncio.sleep(float(2**attempt))
                    continue
                raise NetworkError(f"Azure OpenAI stream connection failed: {exc}") from exc

    def _build_payload(
        self,
        messages: list[Message] | list[dict[str, Any]],
        tools: list[Tool] | list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build Azure OpenAI chat completion request body."""
        formatted_messages: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, Message):
                item: dict[str, Any] = {"role": msg.role.value, "content": msg.content}
                if msg.name:
                    item["name"] = msg.name
                formatted_messages.append(item)
            elif isinstance(msg, dict):
                raw_role = msg.get("role", "user")
                role_str = raw_role.value if isinstance(raw_role, Role) else str(raw_role)
                item = {"role": role_str, "content": str(msg.get("content", ""))}
                if msg.get("name"):
                    item["name"] = str(msg["name"])
                formatted_messages.append(item)
            else:
                raise ValueError(f"Unsupported message type: {type(msg)}")

        payload: dict[str, Any] = {
            "messages": formatted_messages,
            "temperature": float(temperature),
        }
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)

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
        """Parse Azure OpenAI chat completion JSON payload."""
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""

        tool_calls: list[ToolCall] | None = None
        if message.get("tool_calls"):
            tool_calls = [
                ToolCall(
                    id=str(tc["id"]),
                    name=str(tc["function"]["name"]),
                    arguments=tc["function"]["arguments"],
                )
                for tc in message["tool_calls"]
            ]

        usage: Usage | None = None
        if isinstance(data.get("usage"), dict):
            u = data["usage"]
            usage = Usage(
                input_tokens=int(u.get("prompt_tokens", 0)),
                output_tokens=int(u.get("completion_tokens", 0)),
                total_tokens=int(u.get("total_tokens", 0)),
            )

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            model=str(data.get("model") or fallback_model),
        )

    def calculate_cost(self, usage: Usage, model: str | None = None) -> float:
        """Calculate cost in USD from token usage."""
        resolved = (model or self.model).lower()
        pricing = self.PRICING.get(resolved)
        if pricing is None:
            for prefix, rates in self.PRICING.items():
                if resolved.startswith(prefix):
                    pricing = rates
                    break
        if pricing is None:
            pricing = self.PRICING["gpt-4o"]

        return (usage.prompt_tokens / 1_000_000) * pricing["input"] + (
            usage.completion_tokens / 1_000_000
        ) * pricing["output"]

    async def close(self) -> None:
        """Close underlying HTTP client."""
        await self._client.aclose()


AzureOpenAIAdapter = AzureOpenAIModelAdapter
