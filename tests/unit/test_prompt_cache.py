"""
Unit tests for PromptCache, Memory/SQLite/Redis/Tiered backends, and SimilarityMatcher (EVO-025).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from product_value_system.adapters.model_adapter import (
    Message,
    ModelResponse,
    Role,
    Tool,
    ToolCall,
    Usage,
)
from product_value_system.caching import (
    CachedResponse,
    MemoryCacheBackend,
    PromptCache,
    RedisCacheBackend,
    SQLiteCacheBackend,
    TieredCacheBackend,
    cached_llm_call,
    cosine_similarity,
    deserialize_model_response,
    local_prompt_embedding,
    serialize_model_response,
)
from product_value_system.security.secret_detection import SecretDetector

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class _FakeAsyncRedis:
    """Minimal in-memory async Redis stub for testing RedisCacheBackend without a server."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                self.ttls.pop(key, None)
                removed += 1
        return removed

    async def keys(self, pattern: str) -> list[str]:
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]


@pytest.fixture
def cache() -> PromptCache:
    return PromptCache(backend=MemoryCacheBackend(), enable_semantic_matching=False)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_miss_returns_none(cache: PromptCache) -> None:
    """Uncached prompt returns None and increments misses counter."""
    result = await cache.get([{"role": "user", "content": "Hello"}], "gpt-4", 0.0, 1000)
    assert result is None
    assert cache.misses == 1
    assert cache.hits == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_hit_returns_stored_response_and_tracks_tokens(cache: PromptCache) -> None:
    """Stored response is returned on exact cache hit and updates hit count & tokens_saved."""
    messages = [{"role": "user", "content": "Hello"}]
    response = {
        "content": "Hi there!",
        "finish_reason": "stop",
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }

    await cache.set(messages, "gpt-4", 0.0, 1000, response)
    cached = await cache.get(messages, "gpt-4", 0.0, 1000)

    assert cached == response
    assert cache.hits == 1
    assert cache.tokens_saved == 15


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_expiration_after_ttl() -> None:
    """Expired cache entries are treated as misses and removed from backend."""
    backend = MemoryCacheBackend()
    cache = PromptCache(backend=backend, ttl_seconds=0, enable_semantic_matching=False)
    messages = [{"role": "user", "content": "Expiring prompt"}]

    await cache.set(messages, "gpt-4", 0.0, 500, {"content": "old"})
    await asyncio.sleep(0.02)

    assert await cache.get(messages, "gpt-4", 0.0, 500) is None
    assert await backend.size() == 0


@pytest.mark.unit
def test_cache_key_computation_consistency_and_sensitivity(cache: PromptCache) -> None:
    """Cache key is consistent across dict/Message forms and differs on temp, model, or tools."""
    dict_msgs = [{"role": "user", "content": "Explain PIX"}]
    dc_msgs = [Message(role=Role.USER, content="Explain PIX")]

    k_dict = cache.compute_cache_key(dict_msgs, "gpt-4o", 0.001, 500)
    k_dc = cache.compute_cache_key(dc_msgs, "gpt-4o", 0.004, 500)
    assert k_dict == k_dc  # Both temperatures round to 0.00

    k_diff_temp = cache.compute_cache_key(dict_msgs, "gpt-4o", 0.5, 500)
    k_diff_model = cache.compute_cache_key(dict_msgs, "claude-3-5-sonnet-20241022", 0.0, 500)
    k_with_tool = cache.compute_cache_key(
        dict_msgs,
        "gpt-4o",
        0.0,
        500,
        tools=[Tool(name="lookup", description="Lookup data", parameters={})],
    )
    assert len({k_dict, k_diff_temp, k_diff_model, k_with_tool}) == 4


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hit_miss_statistics_tracking(cache: PromptCache) -> None:
    """get_stats reports exact hits, misses, total requests, and hit_rate."""
    msgs = [{"role": "user", "content": "Stat check"}]
    await cache.get(msgs, "gpt-4", 0.0, 100)  # miss
    await cache.set(msgs, "gpt-4", 0.0, 100, {"content": "ok"})
    await cache.get(msgs, "gpt-4", 0.0, 100)  # hit
    await cache.get(msgs, "gpt-4", 0.0, 100)  # hit

    stats = cache.get_stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert stats["total_requests"] == 3
    assert stats["hit_rate"] == pytest.approx(2 / 3, abs=0.001)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_backend_crud_and_lru_eviction() -> None:
    """MemoryCacheBackend supports get/set/delete/clear and evicts least-recently-used entries."""
    backend = MemoryCacheBackend(max_entries=2)
    now = datetime.now(UTC)

    e1 = CachedResponse({"c": 1}, "k1", "h1", "gpt-4", 0.0, now, 3600)
    e2 = CachedResponse({"c": 2}, "k2", "h2", "gpt-4", 0.0, now, 3600)
    e3 = CachedResponse({"c": 3}, "k3", "h3", "gpt-4", 0.0, now, 3600)

    await backend.set("k1", e1)
    await backend.set("k2", e2)
    # Touch k1 so k2 becomes least-recently-used
    assert await backend.get("k1") is not None
    await backend.set("k3", e3)

    assert await backend.size() == 2
    assert await backend.get("k2") is None  # Evicted
    assert await backend.get("k1") is not None
    assert await backend.get("k3") is not None

    await backend.delete("k1")
    assert await backend.size() == 1
    await backend.clear()
    assert await backend.size() == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sqlite_backend_persistence_crud_and_lru(tmp_path: Path) -> None:
    """SQLiteCacheBackend persists across instances, enforces LRU max_entries, and clears."""
    db_file = tmp_path / "cache" / "llm_cache.db"
    backend = SQLiteCacheBackend(db_path=db_file, max_entries=2)
    now = datetime.now(UTC)

    await backend.set(
        "k1",
        CachedResponse(
            {"content": "v1"}, "k1", "h1", "gpt-4", 0.0, now, 3600, embedding=[1.0, 0.0]
        ),
    )
    await asyncio.sleep(0.01)
    await backend.set(
        "k2",
        CachedResponse({"content": "v2"}, "k2", "h2", "gpt-4", 0.0, now, 3600),
    )

    # New instance reading the same SQLite database sees persisted entries
    reopened = SQLiteCacheBackend(db_path=db_file, max_entries=2)
    loaded = await reopened.get("k1")
    assert loaded is not None
    assert loaded.response == {"content": "v1"}
    assert loaded.embedding == [1.0, 0.0]

    # Adding k3 evicts k2 (least recently accessed since k1 was just read)
    await asyncio.sleep(0.01)
    await reopened.set(
        "k3",
        CachedResponse({"content": "v3"}, "k3", "h3", "claude-3-5-sonnet", 0.0, now, 3600),
    )
    assert await reopened.size() == 2
    assert await reopened.get("k2") is None
    assert await reopened.get("k1") is not None

    removed = await reopened.delete_by_model("gpt-4")
    assert removed == 1
    assert await reopened.size() == 1

    await reopened.clear()
    assert await reopened.size() == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_redis_backend_crud_ttl_and_model_invalidation() -> None:
    """RedisCacheBackend serializes entries with TTL, lists by model, and clears prefix."""
    fake_redis = _FakeAsyncRedis()
    backend = RedisCacheBackend(client=fake_redis)
    now = datetime.now(UTC)

    e1 = CachedResponse({"content": "r1"}, "rk1", "h1", "gpt-4o", 0.0, now, 1200)
    e2 = CachedResponse({"content": "r2"}, "rk2", "h2", "claude-3", 0.0, now, 2400)

    await backend.set("rk1", e1)
    await backend.set("rk2", e2)

    assert fake_redis.ttls["llm_cache:rk1"] == 1200
    fetched = await backend.get("rk1")
    assert fetched is not None and fetched.response == {"content": "r1"}
    assert await backend.size() == 2

    removed = await backend.delete_by_model("gpt-4o")
    assert removed == 1
    assert await backend.get("rk1") is None
    assert await backend.get("rk2") is not None

    await backend.clear()
    assert await backend.size() == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tiered_backend_l2_hit_backfills_l1(tmp_path: Path) -> None:
    """TieredCacheBackend backfills L1 memory when serving a hit from L2 SQLite."""
    l1 = MemoryCacheBackend()
    l2 = SQLiteCacheBackend(tmp_path / "l2.db")
    tiered = TieredCacheBackend(l1=l1, l2=l2)
    now = datetime.now(UTC)

    entry = CachedResponse({"content": "tiered"}, "tk1", "h", "gpt-4", 0.0, now, 3600)
    await l2.set("tk1", entry)
    assert await l1.get("tk1") is None

    result = await tiered.get("tk1")
    assert result is not None and result.response == {"content": "tiered"}
    assert await l1.get("tk1") is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_model_version_invalidation() -> None:
    """invalidate_model purges entries for old model, and model_version isolates cache keys."""
    cache = PromptCache(backend=MemoryCacheBackend(), enable_semantic_matching=False)
    msgs = [{"role": "user", "content": "Review spec"}]

    await cache.set(msgs, "gpt-4", 0.0, 500, {"content": "v1"}, model_version="2025-01")
    assert await cache.get(msgs, "gpt-4", 0.0, 500, model_version="2025-01") == {"content": "v1"}
    assert await cache.get(msgs, "gpt-4", 0.0, 500, model_version="2025-06") is None

    removed = await cache.invalidate_model("gpt-4")
    assert removed == 1
    assert await cache.get(msgs, "gpt-4", 0.0, 500, model_version="2025-01") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_semantic_similarity_matching_hit_and_miss() -> None:
    """Semantically similar deterministic prompts hit cache; unrelated prompts miss."""
    cache = PromptCache(
        backend=MemoryCacheBackend(),
        similarity_threshold=0.82,
        enable_semantic_matching=True,
    )
    p1 = [{"role": "user", "content": "Generate unit tests for function def add(a, b): return a+b"}]
    p2 = [{"role": "user", "content": "Generate unit tests for function def add(x, y): return x+y"}]
    p3 = [{"role": "user", "content": "Summarize Q4 retail banking regulatory compliance risks"}]

    await cache.set(p1, "gpt-4o", 0.0, 500, {"content": "def test_add(): assert add(1, 2) == 3"})

    sim_hit = await cache.get(p2, "gpt-4o", 0.0, 500)
    assert sim_hit is not None
    assert cache.semantic_hits == 1

    # High temperature disables semantic match to preserve sampling diversity
    high_temp_miss = await cache.get(p2, "gpt-4o", 0.7, 500)
    assert high_temp_miss is None

    unrelated_miss = await cache.get(p3, "gpt-4o", 0.0, 500)
    assert unrelated_miss is None


@pytest.mark.unit
def test_local_prompt_embedding_and_cosine_similarity() -> None:
    """Local prompt embedding yields 1.0 for identical text and low similarity for unrelated."""
    v1 = local_prompt_embedding("Generate unit tests for payment validator")
    v2 = local_prompt_embedding("Generate unit tests for payment validator")
    v3 = local_prompt_embedding("Deploy kubernetes ingress helm charts")

    assert cosine_similarity(v1, v2) == pytest.approx(1.0)
    assert cosine_similarity(v1, v3) < 0.4
    assert cosine_similarity([], v1) == 0.0


@pytest.mark.unit
def test_model_response_serialization_round_trip() -> None:
    """serialize_model_response / deserialize_model_response preserve Usage and ToolCalls."""
    original = ModelResponse(
        content="Calling tool",
        finish_reason="tool_calls",
        model="claude-3-5-sonnet-20241022",
        usage=Usage(input_tokens=120, output_tokens=45, total_tokens=165),
        tool_calls=[ToolCall(id="tc_1", name="search_kb", arguments={"query": "PIX"})],
    )
    restored = deserialize_model_response(serialize_model_response(original))

    assert restored.content == original.content
    assert restored.finish_reason == "tool_calls"
    assert restored.model == original.model
    assert restored.usage == original.usage
    assert restored.tool_calls is not None and len(restored.tool_calls) == 1
    assert restored.tool_calls[0].name == "search_kb"
    assert restored.tool_calls[0].arguments == {"query": "PIX"}


@pytest.mark.unit
def test_prompt_cache_from_config_and_disabled(tmp_path: Path) -> None:
    """PromptCache.from_config loads config/caching.yaml and returns None when disabled."""
    loaded = PromptCache.from_config(REPO_ROOT / "config" / "caching.yaml")
    assert loaded is not None
    assert loaded.ttl_seconds == 604800
    assert loaded.similarity_threshold == pytest.approx(0.95)

    disabled_cfg = tmp_path / "disabled.yaml"
    disabled_cfg.write_text("caching:\n  enabled: false\n", encoding="utf-8")
    assert PromptCache.from_config(disabled_cfg) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cached_llm_call_decorator() -> None:
    """@cached_llm_call caches deterministic calls and bypasses cache when temperature >= 0.3."""
    cache = PromptCache(backend=MemoryCacheBackend(), enable_semantic_matching=False)

    class _DummyAdapter:
        model = "gpt-4o"

        def __init__(self) -> None:
            self.calls = 0

        @cached_llm_call(cache=cache)
        async def invoke(
            self,
            messages: list[Any],
            tools: Any = None,
            temperature: float = 0.7,
            max_tokens: int | None = None,
            **kwargs: Any,
        ) -> ModelResponse:
            self.calls += 1
            return ModelResponse(
                content=f"response-{self.calls}",
                finish_reason="stop",
                model=self.model,
                usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            )

    adapter = _DummyAdapter()
    r1 = await adapter.invoke([{"role": "user", "content": "Hi"}], temperature=0.0)
    r2 = await adapter.invoke([{"role": "user", "content": "Hi"}], temperature=0.0)
    r3 = await adapter.invoke([{"role": "user", "content": "Hi"}], temperature=0.8)

    assert r1.content == "response-1"
    assert r2.content == "response-1"  # Served from cache
    assert r3.content == "response-2"  # High temp bypassed cache
    assert adapter.calls == 2


@pytest.mark.unit
def test_cached_response_expired_check_with_past_timestamp() -> None:
    """CachedResponse.is_expired detects aged entries accurately."""
    old = CachedResponse(
        response={},
        cache_key="k",
        prompt_hash="h",
        model="gpt-4",
        temperature=0.0,
        cached_at=datetime.now(UTC) - timedelta(hours=2),
        ttl_seconds=3600,
    )
    assert old.is_expired() is True


@pytest.mark.unit
def test_caching_config_and_docs_contain_zero_secrets() -> None:
    """EVO-025 config and documentation contain zero detectable secrets."""
    detector = SecretDetector()
    for rel in ("config/caching.yaml", "docs/token-caching.md"):
        content = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert detector.find_secrets(content) == [], rel
