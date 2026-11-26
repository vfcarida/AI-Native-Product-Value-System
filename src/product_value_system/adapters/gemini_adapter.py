"""
Native Google Gemini model adapter implementation (v1.0).

Speaks the Google Generative Language REST API directly over ``httpx`` with:
- Header-based authentication (``x-goog-api-key``; never exposed in query strings)
- System instruction translation and multi-turn conversation mapping
- Native function/tool calling (``functionDeclarations`` / ``functionCall``)
- Real Server-Sent Events (SSE) streaming via ``:streamGenerateContent?alt=sse``
- Rate-limit (HTTP 429) & transient 5xx retry handling with secret-redacted errors
- Optional deterministic ``PromptCache`` integration and USD cost tracking
"""

from __future__ import annotations

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

_FINISH_REASON_MAP: dict[str, str] = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "OTHER": "stop",
}


class GeminiModelAdapter:
    """Native adapter for Google Gemini models (gemini-2.5-pro, gemini-2.5-flash, etc.)."""

    # Pricing in USD per 1M tokens (standard context tier)
    PRICING: dict[str, dict[str, float]] = {
        "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
        "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
        "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
        "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30},
        "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
        "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    }

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: float = 60.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        cache: PromptCache | None = None,
        cache_max_temperature: float = 0.3,
    ) -> None:
        resolved_key = resolve_secret(
            vault_path="product-value-system/gemini",
            key="api_key",
            env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            explicit=api_key,
        )
        if not resolved_key and client is None:
            raise ValueError(
                "Gemini API key required (configure HashiCorp Vault or set "
                "GEMINI_API_KEY / GOOGLE_API_KEY env var)"
            )

        self.api_key = resolved_key or ""
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.max_retries = max(1, int(max_retries))
        self.cache = cache
        self.cache_max_temperature = float(cache_max_temperature)
        self._secret_detector = SecretDetector()

        if client is not None:
            self._client = client
        else:
            client_kwargs: dict[str, Any] = {
                "base_url": self.base_url,
                "headers": {
                    "x-goog-api-key": self.api_key,
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
        messages: list[Message] | list[dict[str, Any]],
        tools: list[Tool] | list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Invoke Gemini ``:generateContent`` API with optional deterministic caching."""
        model_name = str(kwargs.pop("model", None) or self.model)
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
                logger.info("PromptCache hit for Gemini model %s", model_name)
                return deserialize_model_response(cached)

        payload = self._build_payload(messages, tools, temperature, max_tokens, **kwargs)
        endpoint = f"/models/{model_name}:generateContent"

        for attempt in range(self.max_retries):
            try:
                response = await self._client.post(endpoint, json=payload)

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
                    raise ModelError(f"Gemini API error: {response.status_code} {safe_text}")

                data: dict[str, Any] = response.json()
                model_response = self._parse_response(data, model_name=model_name)

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
                    await asyncio.sleep(float(2**attempt))
                    continue
                raise NetworkError(f"Gemini request failed: {exc}") from exc

        raise ModelError("Max retries exceeded for Gemini request")

    async def stream(
        self,
        messages: list[Message] | list[dict[str, Any]],
        tools: list[Tool] | list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelChunk]:
        """Stream response chunks via Gemini ``:streamGenerateContent?alt=sse``."""
        model_name = str(kwargs.pop("model", None) or self.model)
        payload = self._build_payload(messages, tools, temperature, max_tokens, **kwargs)
        endpoint = f"/models/{model_name}:streamGenerateContent"

        for attempt in range(self.max_retries):
            yielded_any = False
            yielded_final = False
            accumulated_usage: ModelUsage | None = None
            finish_reason: str | None = None

            try:
                async with self._client.stream(
                    "POST",
                    endpoint,
                    params={"alt": "sse"},
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
                        raise ModelError(f"Gemini stream error: {response.status_code} {safe_err}")

                    async for raw_line in response.aiter_lines():
                        line = raw_line.strip()
                        if not line or line.startswith(":") or not line.startswith("data:"):
                            continue

                        data_str = line[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            break

                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        usage_meta = data.get("usageMetadata")
                        if isinstance(usage_meta, dict):
                            p_tok = int(usage_meta.get("promptTokenCount", 0))
                            c_tok = int(usage_meta.get("candidatesTokenCount", 0))
                            t_tok = int(usage_meta.get("totalTokenCount", p_tok + c_tok))
                            accumulated_usage = ModelUsage(
                                input_tokens=p_tok,
                                output_tokens=c_tok,
                                total_tokens=t_tok,
                            )

                        candidates = data.get("candidates") or []
                        if not candidates:
                            continue

                        cand = candidates[0]
                        parts = (cand.get("content") or {}).get("parts") or []
                        text_delta = "".join(
                            str(part.get("text", ""))
                            for part in parts
                            if isinstance(part, dict) and "text" in part
                        )
                        raw_finish = cand.get("finishReason")
                        chunk_finish = (
                            _FINISH_REASON_MAP.get(str(raw_finish).upper(), str(raw_finish).lower())
                            if raw_finish
                            else None
                        )
                        if chunk_finish:
                            finish_reason = chunk_finish

                        if text_delta or chunk_finish:
                            yielded_any = True
                            if chunk_finish:
                                yielded_final = True
                            yield ModelChunk(
                                delta=text_delta,
                                finish_reason=chunk_finish,
                                usage=accumulated_usage if chunk_finish else None,
                            )

                if not yielded_final and (yielded_any or accumulated_usage is not None):
                    yield ModelChunk(
                        delta="",
                        finish_reason=finish_reason or "stop",
                        usage=accumulated_usage
                        or ModelUsage(input_tokens=0, output_tokens=0, total_tokens=0),
                    )
                return

            except httpx.RequestError as exc:
                if not yielded_any and attempt < self.max_retries - 1:
                    await asyncio.sleep(float(2**attempt))
                    continue
                raise NetworkError(f"Gemini stream connection failed: {exc}") from exc

    def _build_payload(
        self,
        messages: list[Message] | list[dict[str, Any]],
        tools: list[Tool] | list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Translate unified ``Message``/``Tool`` list into Gemini ``generateContent`` payload."""
        response_schema = kwargs.pop("response_schema", None)
        cached_content = kwargs.pop("cached_content", None)

        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []

        for msg in messages:
            if isinstance(msg, Message):
                role_str = msg.role.value
                content_str = msg.content
                tool_name = msg.name
            elif isinstance(msg, dict):
                raw_role = msg.get("role", "user")
                role_str = raw_role.value if isinstance(raw_role, Role) else str(raw_role)
                content_str = str(msg.get("content", ""))
                tool_name = msg.get("name")
            else:
                raise ValueError(f"Unsupported message type: {type(msg)}")

            if role_str == "system":
                if content_str:
                    system_parts.append(content_str)
            elif role_str == "assistant":
                contents.append({"role": "model", "parts": [{"text": content_str}]})
            elif role_str == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": str(tool_name or "tool"),
                                    "response": {"output": content_str},
                                }
                            }
                        ],
                    }
                )
            else:
                contents.append({"role": "user", "parts": [{"text": content_str}]})

        generation_config: dict[str, Any] = {"temperature": float(temperature)}
        if max_tokens is not None:
            generation_config["maxOutputTokens"] = int(max_tokens)
        if "top_p" in kwargs:
            generation_config["topP"] = float(kwargs.pop("top_p"))
        if "stop_sequences" in kwargs:
            generation_config["stopSequences"] = list(kwargs.pop("stop_sequences"))
        if response_schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = self._normalize_gemini_schema(response_schema)

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if cached_content:
            payload["cachedContent"] = str(cached_content)

        if tools:
            declarations: list[dict[str, Any]] = []
            for tool in tools:
                if isinstance(tool, Tool):
                    declarations.append(
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.parameters,
                        }
                    )
                elif isinstance(tool, dict):
                    if "function" in tool and isinstance(tool["function"], dict):
                        fn = tool["function"]
                        declarations.append(
                            {
                                "name": fn["name"],
                                "description": fn.get("description", ""),
                                "parameters": fn.get("parameters", {"type": "object"}),
                            }
                        )
                    else:
                        declarations.append(tool)
            if declarations:
                payload["tools"] = [{"functionDeclarations": declarations}]

        payload.update(kwargs)
        return payload

    @classmethod
    def _normalize_gemini_schema(cls, schema_or_model: Any) -> dict[str, Any]:
        """Convert Pydantic model or JSON Schema dict into Gemini ``responseSchema`` subset."""
        if isinstance(schema_or_model, type) and hasattr(schema_or_model, "model_json_schema"):
            raw = dict(schema_or_model.model_json_schema())
        elif isinstance(schema_or_model, dict):
            raw = dict(schema_or_model)
        else:
            raise ValueError(f"Unsupported response_schema type: {type(schema_or_model)}")
        cleaned = cls._strip_unsupported_schema_keys(raw)
        return dict(cleaned) if isinstance(cleaned, dict) else {}

    @classmethod
    def _strip_unsupported_schema_keys(cls, node: Any) -> Any:
        if isinstance(node, dict):
            cleaned: dict[str, Any] = {}
            for k, v in node.items():
                if k in {"additionalProperties", "$schema", "title"}:
                    continue
                cleaned[k] = cls._strip_unsupported_schema_keys(v)
            return cleaned
        if isinstance(node, list):
            return [cls._strip_unsupported_schema_keys(item) for item in node]
        return node

    def _parse_response(self, data: dict[str, Any], *, model_name: str) -> ModelResponse:
        """Parse Gemini ``generateContent`` JSON response including context cache telemetry."""
        candidates = data.get("candidates") or []
        text_chunks: list[str] = []
        tool_calls: list[ToolCall] = []
        raw_finish: str | None = None

        if candidates:
            cand = candidates[0]
            raw_finish = cand.get("finishReason")
            parts = (cand.get("content") or {}).get("parts") or []
            for idx, part in enumerate(parts):
                if not isinstance(part, dict):
                    continue
                if "text" in part and part["text"] is not None:
                    text_chunks.append(str(part["text"]))
                if "functionCall" in part and isinstance(part["functionCall"], dict):
                    fc = part["functionCall"]
                    tool_calls.append(
                        ToolCall(
                            id=f"gemini_call_{idx}",
                            name=str(fc.get("name", "")),
                            arguments=json.dumps(fc.get("args", {})),
                        )
                    )

        usage: Usage | None = None
        usage_meta = data.get("usageMetadata")
        if isinstance(usage_meta, dict):
            p_tok = int(usage_meta.get("promptTokenCount", 0))
            c_tok = int(usage_meta.get("candidatesTokenCount", 0))
            t_tok = int(usage_meta.get("totalTokenCount", p_tok + c_tok))
            cached_tok = int(usage_meta.get("cachedContentTokenCount", 0))
            usage = Usage(
                input_tokens=p_tok,
                output_tokens=c_tok,
                total_tokens=t_tok,
                cache_read_tokens=cached_tok,
            )

        finish_reason = (
            "tool_calls"
            if tool_calls
            else _FINISH_REASON_MAP.get(str(raw_finish or "STOP").upper(), "stop")
        )
        return ModelResponse(
            content="".join(text_chunks),
            tool_calls=tool_calls or None,
            usage=usage,
            finish_reason=finish_reason,
            model=str(data.get("modelVersion") or model_name),
        )

    def calculate_cost(self, usage: Usage, model: str | None = None) -> float:
        """Calculate cost in USD including 75% Gemini context-cache read discount."""
        resolved = (model or self.model).lower()
        pricing = self.PRICING.get(resolved)
        if pricing is None:
            # Prefix match for versioned snapshots (e.g., gemini-2.5-flash-preview-04-17)
            for prefix, rates in self.PRICING.items():
                if resolved.startswith(prefix):
                    pricing = rates
                    break
        if pricing is None:
            pricing = self.PRICING["gemini-2.5-flash"]

        cached_tokens = min(usage.cache_read_tokens, usage.input_tokens)
        uncached_input = max(0, usage.input_tokens - cached_tokens)
        return (
            (uncached_input / 1_000_000) * pricing["input"]
            + (cached_tokens / 1_000_000) * (pricing["input"] * 0.25)
            + (usage.output_tokens / 1_000_000) * pricing["output"]
        )

    async def close(self) -> None:
        """Close underlying HTTP client."""
        await self._client.aclose()


GeminiAdapter = GeminiModelAdapter
