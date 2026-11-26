"""
Semantic LLM Prompt Cache with exact-match fast path, embedding similarity fallback,
TTL expiration, model-version invalidation, and ModelResponse serialization (EVO-025).
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from product_value_system.adapters.model_adapter import (
    Message,
    ModelResponse,
    Role,
    Tool,
    ToolCall,
    Usage,
)
from product_value_system.caching.cache_backends import (
    CacheBackend,
    CachedResponse,
    MemoryCacheBackend,
    RedisCacheBackend,
    SQLiteCacheBackend,
    TieredCacheBackend,
)
from product_value_system.caching.similarity_matcher import (
    EmbeddingFunc,
    SimilarityMatcher,
)


def _normalize_messages(messages: Sequence[Message | dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, Message):
            item: dict[str, Any] = {"role": msg.role.value, "content": msg.content}
            if msg.name:
                item["name"] = msg.name
            normalized.append(item)
        else:
            role = msg.get("role", "")
            role_str = role.value if isinstance(role, Role) else str(role)
            item = {"role": role_str, "content": str(msg.get("content", ""))}
            if msg.get("name"):
                item["name"] = str(msg["name"])
            normalized.append(item)
    return normalized


def _normalize_tools(tools: Sequence[Tool | dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not tools:
        return []
    normalized: list[dict[str, Any]] = []
    for tool in tools:
        if isinstance(tool, Tool):
            normalized.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
            )
        else:
            normalized.append(dict(tool))
    return normalized


def serialize_model_response(response: ModelResponse) -> dict[str, Any]:
    """Losslessly serialize ``ModelResponse`` (including Usage and ToolCalls) to JSON dict."""
    usage_dict: dict[str, int] | None = None
    if response.usage is not None:
        usage_dict = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
        }

    tool_calls_list: list[dict[str, Any]] | None = None
    if response.tool_calls is not None:
        tool_calls_list = []
        for tc in response.tool_calls:
            if isinstance(tc, ToolCall):
                tool_calls_list.append(
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                )
            elif isinstance(tc, dict):
                tool_calls_list.append(dict(tc))

    return {
        "content": response.content,
        "finish_reason": response.finish_reason,
        "model": response.model,
        "usage": usage_dict,
        "tool_calls": tool_calls_list,
    }


def deserialize_model_response(data: dict[str, Any]) -> ModelResponse:
    """Reconstruct ``ModelResponse`` (preserving Usage and ToolCalls) from cached dict."""
    usage_raw = data.get("usage")
    usage: Usage | None = None
    if isinstance(usage_raw, dict):
        usage = Usage(
            input_tokens=usage_raw.get("input_tokens", usage_raw.get("prompt_tokens")),
            output_tokens=usage_raw.get("output_tokens", usage_raw.get("completion_tokens")),
            total_tokens=usage_raw.get("total_tokens"),
        )

    tc_raw = data.get("tool_calls")
    tool_calls: list[ToolCall] | None = None
    if isinstance(tc_raw, list):
        tool_calls = [
            ToolCall(
                id=str(tc.get("id", "")),
                name=str(tc.get("name", "")),
                arguments=tc.get("arguments"),
            )
            for tc in tc_raw
            if isinstance(tc, dict)
        ]

    return ModelResponse(
        content=str(data.get("content", "")),
        tool_calls=tool_calls,
        usage=usage,
        finish_reason=data.get("finish_reason"),
        model=data.get("model"),
    )


def _fast_hash(payload: bytes) -> str:
    try:
        import xxhash

        return str(xxhash.xxh64(payload).hexdigest())
    except ImportError:
        return hashlib.sha256(payload).hexdigest()[:16]


class PromptCache:
    """Hybrid exact + semantic LLM response cache with TTL, LRU, and metrics."""

    def __init__(
        self,
        backend: CacheBackend | None = None,
        ttl_seconds: int = 604800,  # 7 days
        similarity_threshold: float = 0.95,
        enable_semantic_matching: bool = True,
        *,
        semantic_max_temperature: float = 0.3,
        embedding_func: EmbeddingFunc | None = None,
        embedding_adapter: Any | None = None,
        invalidate_on_model_change: bool = True,
    ) -> None:
        self.backend: CacheBackend = backend or MemoryCacheBackend()
        self.ttl_seconds = int(ttl_seconds)
        self.similarity_threshold = float(similarity_threshold)
        self.enable_semantic_matching = enable_semantic_matching
        self.semantic_max_temperature = float(semantic_max_temperature)
        self.invalidate_on_model_change = invalidate_on_model_change
        resolved_embed_fn: EmbeddingFunc | None = embedding_func
        if resolved_embed_fn is None and embedding_adapter is not None:
            resolved_embed_fn = embedding_adapter.embed
        self.embedding_adapter = embedding_adapter
        self.similarity_matcher = SimilarityMatcher(embedding_func=resolved_embed_fn)

        # Telemetry metrics
        self.hits = 0
        self.misses = 0
        self.semantic_hits = 0
        self.tokens_saved = 0

    @classmethod
    def from_config(cls, config_path: Path | str) -> PromptCache | None:
        """Instantiate PromptCache from ``config/caching.yaml`` (returns None if disabled)."""
        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        cfg = raw.get("caching", raw)
        if not cfg.get("enabled", True):
            return None

        backend_type = str(cfg.get("backend", "memory")).strip().lower()
        max_entries = int(cfg.get("max_entries", 10000))

        backend: CacheBackend
        if backend_type == "sqlite":
            sqlite_cfg = cfg.get("sqlite", {}) or {}
            backend = SQLiteCacheBackend(
                db_path=sqlite_cfg.get("path", ".cache/llm_cache.db"),
                max_entries=max_entries,
            )
        elif backend_type == "redis":
            redis_cfg = cfg.get("redis", {}) or {}
            backend = RedisCacheBackend(
                host=str(redis_cfg.get("host", "localhost")),
                port=int(redis_cfg.get("port", 6379)),
                db=int(redis_cfg.get("db", 0)),
                password=redis_cfg.get("password"),
            )
        elif backend_type == "tiered":
            sqlite_cfg = cfg.get("sqlite", {}) or {}
            backend = TieredCacheBackend(
                l1=MemoryCacheBackend(max_entries=min(1000, max_entries)),
                l2=SQLiteCacheBackend(
                    db_path=sqlite_cfg.get("path", ".cache/llm_cache.db"),
                    max_entries=max_entries,
                ),
            )
        else:
            backend = MemoryCacheBackend(max_entries=max_entries)

        return cls(
            backend=backend,
            ttl_seconds=int(cfg.get("ttl_seconds", 604800)),
            similarity_threshold=float(cfg.get("similarity_threshold", 0.95)),
            enable_semantic_matching=bool(cfg.get("enable_semantic_matching", True)),
            invalidate_on_model_change=bool(cfg.get("invalidate_on_model_change", True)),
        )

    def compute_cache_key(
        self,
        messages: Sequence[Message | dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        tools: Sequence[Tool | dict[str, Any]] | None = None,
        *,
        model_version: str | None = None,
        prompt_version: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Compute deterministic xxhash64 cache key from normalized request parameters."""
        temp = float(temperature)
        if not math.isfinite(temp):
            raise ValueError(f"temperature must be a finite float, got {temperature!r}")
        key_data: dict[str, Any] = {
            "messages": _normalize_messages(messages),
            "model": model,
            "temperature": round(temp, 2),
            "max_tokens": max_tokens,
            "tools": _normalize_tools(tools),
        }
        if self.invalidate_on_model_change and model_version:
            key_data["model_version"] = model_version
        if prompt_version:
            key_data["prompt_version"] = prompt_version
        if tenant_id:
            key_data["tenant_id"] = tenant_id
        serialized = json.dumps(key_data, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return _fast_hash(serialized.encode("utf-8"))

    async def get(
        self,
        messages: Sequence[Message | dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        tools: Sequence[Tool | dict[str, Any]] | None = None,
        *,
        model_version: str | None = None,
        prompt_version: str | None = None,
        tenant_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Look up exact match first, then temporal/tenant/version-scoped semantic match."""
        cache_key = self.compute_cache_key(
            messages,
            model,
            temperature,
            max_tokens,
            tools,
            model_version=model_version,
            prompt_version=prompt_version,
            tenant_id=tenant_id,
        )
        cached = await self.backend.get(cache_key)

        if cached is not None:
            if cached.is_expired(now=now):
                await self.backend.delete(cache_key)
            else:
                cached.hit_count += 1
                self.hits += 1
                self._record_saved_tokens(cached.response)
                return cached.response

        # Temporal + version + tenant-scoped semantic similarity fallback
        if self.enable_semantic_matching and temperature < self.semantic_max_temperature:
            semantic_key = await self._find_similar_prompt(
                messages,
                model,
                prompt_version=prompt_version or "",
                tenant_id=tenant_id or "",
                now=now,
            )
            if semantic_key is not None:
                sem_cached = await self.backend.get(semantic_key)
                if sem_cached is not None and not sem_cached.is_expired(now=now):
                    sem_cached.hit_count += 1
                    self.semantic_hits += 1
                    self.hits += 1
                    self._record_saved_tokens(sem_cached.response)
                    return sem_cached.response

        self.misses += 1
        return None

    async def set(
        self,
        messages: Sequence[Message | dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        response: dict[str, Any],
        tools: Sequence[Tool | dict[str, Any]] | None = None,
        *,
        model_version: str | None = None,
        prompt_version: str | None = None,
        tenant_id: str | None = None,
        valid_until: datetime | None = None,
    ) -> None:
        """Store response with prompt hash, temporal/version metadata, and semantic embedding."""
        cache_key = self.compute_cache_key(
            messages,
            model,
            temperature,
            max_tokens,
            tools,
            model_version=model_version,
            prompt_version=prompt_version,
            tenant_id=tenant_id,
        )
        prompt_text = self._messages_to_text(messages)
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()

        embedding: list[float] | None = None
        if self.enable_semantic_matching:
            embedding = await self.similarity_matcher.embed(prompt_text)

        entry = CachedResponse(
            response=response,
            cache_key=cache_key,
            prompt_hash=prompt_hash,
            model=model,
            temperature=float(temperature),
            cached_at=datetime.now(UTC),
            ttl_seconds=self.ttl_seconds,
            hit_count=0,
            embedding=embedding,
            prompt_text=prompt_text,
            prompt_version=prompt_version or "",
            tenant_id=tenant_id or "",
            valid_until=valid_until,
        )
        await self.backend.set(cache_key, entry)

    async def invalidate_model(self, model: str) -> int:
        """Delete all cached entries belonging to ``model`` (model upgrade invalidation)."""
        return await self.backend.delete_by_model(model)

    async def clear(self) -> None:
        """Clear all entries from the backing store."""
        await self.backend.clear()

    async def _find_similar_prompt(
        self,
        messages: Sequence[Message | dict[str, Any]],
        model: str,
        *,
        prompt_version: str = "",
        tenant_id: str = "",
        now: datetime | None = None,
    ) -> str | None:
        entries = await self.backend.list_entries(model=model)
        candidates: list[tuple[str, list[float]]] = []
        for entry in entries:
            if entry.is_expired(now=now) or not entry.embedding:
                continue
            if prompt_version and entry.prompt_version != prompt_version:
                continue
            if tenant_id and entry.tenant_id != tenant_id:
                continue
            candidates.append((entry.cache_key, entry.embedding))
        if not candidates:
            return None

        prompt_text = self._messages_to_text(messages)
        query_vec = await self.similarity_matcher.embed(prompt_text)
        best = self.similarity_matcher.find_most_similar(
            query_vec,
            candidates,
            threshold=self.similarity_threshold,
        )
        return best[0] if best is not None else None

    @staticmethod
    def _messages_to_text(messages: Sequence[Message | dict[str, Any]]) -> str:
        norm = _normalize_messages(messages)
        return "\n".join(f"{m['role']}: {m['content']}" for m in norm)

    def _record_saved_tokens(self, response: dict[str, Any]) -> None:
        usage = response.get("usage")
        if isinstance(usage, dict):
            self.tokens_saved += int(usage.get("total_tokens", 0) or 0)

    def get_stats(self) -> dict[str, Any]:
        """Return hit/miss, semantic hit rate, and token-savings statistics."""
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "exact_hits": self.hits - self.semantic_hits,
            "semantic_hits": self.semantic_hits,
            "misses": self.misses,
            "total_requests": total,
            "hit_rate": round(self.hits / total, 4) if total > 0 else 0.0,
            "semantic_hit_rate": (
                round(self.semantic_hits / self.hits, 4) if self.hits > 0 else 0.0
            ),
            "tokens_saved": self.tokens_saved,
        }


def cached_llm_call(
    cache: PromptCache | None = None,
    *,
    max_temperature: float = 0.3,
) -> Callable[[Callable[..., Awaitable[ModelResponse]]], Callable[..., Awaitable[ModelResponse]]]:
    """Decorator adding transparent PromptCache lookup/storage to an adapter ``invoke`` method."""

    def decorator(
        func: Callable[..., Awaitable[ModelResponse]],
    ) -> Callable[..., Awaitable[ModelResponse]]:
        @functools.wraps(func)
        async def wrapper(
            self: Any,
            messages: list[Message | dict[str, Any]],
            tools: list[Tool | dict[str, Any]] | None = None,
            temperature: float = 0.7,
            max_tokens: int | None = None,
            **kwargs: Any,
        ) -> ModelResponse:
            active_cache: PromptCache | None = cache or getattr(self, "cache", None)
            model_name = str(kwargs.get("model") or getattr(self, "model", "unknown"))

            if active_cache is not None and temperature < max_temperature:
                hit = await active_cache.get(messages, model_name, temperature, max_tokens, tools)
                if hit is not None:
                    return deserialize_model_response(hit)

            result = await func(
                self,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

            if active_cache is not None and temperature < max_temperature:
                await active_cache.set(
                    messages,
                    model_name,
                    temperature,
                    max_tokens,
                    serialize_model_response(result),
                    tools,
                )
            return result

        return wrapper

    return decorator
