"""
Tests for dense EmbeddingAdapters (OpenAI, Gemini, LocalHash), Temporal &
Prompt-Version/Tenant-Scoped Semantic Caching, and pgvector HNSW migration
(EVO-P3-10).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from product_value_system.adapters.embedding_adapter import (
    GeminiEmbeddingAdapter,
    LocalHashEmbeddingAdapter,
    OpenAIEmbeddingAdapter,
    create_embedding_adapter,
)
from product_value_system.caching.prompt_cache import PromptCache

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_local_openai_and_gemini_embedding_adapters() -> None:
    local = create_embedding_adapter("local", dimension=64)
    assert isinstance(local, LocalHashEmbeddingAdapter)
    vecs = await local.embed_batch(["checkout latency spike", "checkout latency issue"])
    assert len(vecs) == 2
    assert len(vecs[0]) == 64

    def _openai_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/embeddings")
        body = json.loads(request.content.decode("utf-8"))
        n = len(body["input"])
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [0.1 * (i + 1), 0.2, 0.3]}
                    for i in range(n)
                ]
            },
        )

    openai_emb = OpenAIEmbeddingAdapter(
        api_key="test-openai-emb-key",
        dimensions=3,
        transport=httpx.MockTransport(_openai_handler),
    )
    o_vec = await openai_emb.embed("hello world")
    assert o_vec == [0.1, 0.2, 0.3]
    await openai_emb.close()

    def _gemini_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "test-gemini-emb-key"
        if request.url.path.endswith(":batchEmbedContents"):
            return httpx.Response(
                200,
                json={"embeddings": [{"values": [0.5, 0.5]}, {"values": [0.2, 0.8]}]},
            )
        return httpx.Response(200, json={"embedding": {"values": [0.9, 0.1]}})

    gemini_emb = GeminiEmbeddingAdapter(
        api_key="test-gemini-emb-key",
        dimension=2,
        transport=httpx.MockTransport(_gemini_handler),
    )
    assert await gemini_emb.embed("single") == [0.9, 0.1]
    assert await gemini_emb.embed_batch(["a", "b"]) == [[0.5, 0.5], [0.2, 0.8]]
    await gemini_emb.close()


@pytest.mark.asyncio
async def test_temporal_prompt_version_and_tenant_scoped_semantic_cache() -> None:
    """Semantic cache honors valid_until, prompt_version, and tenant_id boundaries."""
    adapter = LocalHashEmbeddingAdapter(dimension=128)
    cache = PromptCache(
        similarity_threshold=0.70,
        enable_semantic_matching=True,
        embedding_adapter=adapter,
    )

    now = datetime.now(UTC)
    expires_soon = now + timedelta(minutes=10)

    await cache.set(
        messages=[{"role": "user", "content": "Analyze checkout API latency regression"}],
        model="gpt-4o",
        temperature=0.0,
        max_tokens=500,
        response={"content": "v1-alpha-analysis"},
        prompt_version="discovery@1.0.0",
        tenant_id="tenant-alpha",
        valid_until=expires_soon,
    )

    # 1. Semantic hit for same tenant, same prompt_version, before valid_until
    hit = await cache.get(
        messages=[{"role": "user", "content": "Analyze checkout API latency regressions"}],
        model="gpt-4o",
        temperature=0.0,
        max_tokens=500,
        prompt_version="discovery@1.0.0",
        tenant_id="tenant-alpha",
        now=now + timedelta(minutes=2),
    )
    assert hit is not None
    assert hit["content"] == "v1-alpha-analysis"
    assert cache.semantic_hits == 1

    # 2. Cross-tenant isolation: tenant-beta MUST NOT receive tenant-alpha's cached entry
    cross_tenant = await cache.get(
        messages=[{"role": "user", "content": "Analyze checkout API latency regressions"}],
        model="gpt-4o",
        temperature=0.0,
        max_tokens=500,
        prompt_version="discovery@1.0.0",
        tenant_id="tenant-beta",
        now=now + timedelta(minutes=2),
    )
    assert cross_tenant is None

    # 3. Prompt version upgrade invalidates semantic match cleanly
    upgraded_prompt = await cache.get(
        messages=[{"role": "user", "content": "Analyze checkout API latency regressions"}],
        model="gpt-4o",
        temperature=0.0,
        max_tokens=500,
        prompt_version="discovery@1.1.0",
        tenant_id="tenant-alpha",
        now=now + timedelta(minutes=2),
    )
    assert upgraded_prompt is None

    # 4. Temporal valid_until expiration invalidates entry after window passes
    expired = await cache.get(
        messages=[{"role": "user", "content": "Analyze checkout API latency regression"}],
        model="gpt-4o",
        temperature=0.0,
        max_tokens=500,
        prompt_version="discovery@1.0.0",
        tenant_id="tenant-alpha",
        now=now + timedelta(minutes=15),
    )
    assert expired is None


def test_pgvector_hnsw_sql_and_alembic_003_migration_present() -> None:
    sql_path = REPO_ROOT / "migrations" / "postgres" / "004_create_pgvector_semantic_store.sql"
    alembic_path = REPO_ROOT / "alembic" / "versions" / "003_create_pgvector_semantic_store.py"

    sql_text = sql_path.read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql_text
    assert "USING hnsw (embedding vector_cosine_ops)" in sql_text
    assert "ENABLE ROW LEVEL SECURITY" in sql_text

    alembic_text = alembic_path.read_text(encoding="utf-8")
    assert 'revision: str = "c3d4e5f60718"' in alembic_text
    assert 'down_revision: str | None = "b2c3d4e5f607"' in alembic_text
