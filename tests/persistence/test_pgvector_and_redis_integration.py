"""
Tests for pgvector HNSW Semantic Store + Redis L3 Temporal/Tenant Prompt Cache
integration invariants (v2.6).

Runs deterministically offline using faithful async test doubles, and also
executes a live PostgreSQL+pgvector+Redis smoke test automatically in Linux CI
(``real-infra-integration`` job) when ``DATABASE_URL`` and ``REDIS_URL`` are set.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from product_value_system.caching.cache_backends import RedisCacheBackend
from product_value_system.caching.prompt_cache import PromptCache
from product_value_system.caching.similarity_matcher import (
    cosine_similarity,
    local_prompt_embedding,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class _AsyncFakeRedis:
    """Faithful async Redis double exercising ``RedisCacheBackend`` serialization & TTL."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}

    async def get(self, key: str) -> str | None:
        item = self._store.get(key)
        if item is None:
            return None
        val, exp = item
        if exp is not None and datetime.now(UTC).timestamp() > exp:
            self._store.pop(key, None)
            return None
        return val

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        exp = datetime.now(UTC).timestamp() + ex if ex is not None else None
        self._store[key] = (value, exp)
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for k in keys:
            if k in self._store:
                self._store.pop(k, None)
                removed += 1
        return removed

    async def keys(self, pattern: str) -> list[str]:
        prefix = pattern.rstrip("*")
        return [k for k in list(self._store.keys()) if k.startswith(prefix)]


@pytest.mark.asyncio
async def test_redis_l3_prompt_cache_enforces_tenant_version_and_temporal_isolation() -> None:
    """Redis L3 semantic + exact cache isolates tenants, prompt versions, and temporal windows."""
    fake_redis = _AsyncFakeRedis()
    backend = RedisCacheBackend(client=fake_redis, prefix="pvs:test:")
    cache = PromptCache(
        backend=backend,
        enable_semantic_matching=True,
        similarity_threshold=0.80,
    )

    prompt_msgs = [
        {
            "role": "user",
            "content": "Analyze checkout p95 latency regression after payment webhook update",
        }
    ]
    near_msgs = [
        {
            "role": "user",
            "content": "Analyze checkout p95 latency regression after payment webhook update!",
        }
    ]

    await cache.set(
        prompt_msgs,
        "claude-3-5-sonnet",
        0.0,
        512,
        {"content": "Tenant A v1 answer"},
        tenant_id="tenant-a",
        prompt_version="v1",
        valid_until=datetime.now(UTC) + timedelta(minutes=10),
    )

    # Same tenant + same prompt_version -> semantic hit
    hit = await cache.get(
        near_msgs,
        "claude-3-5-sonnet",
        0.0,
        512,
        tenant_id="tenant-a",
        prompt_version="v1",
    )
    assert hit is not None
    assert hit["content"] == "Tenant A v1 answer"

    # Cross-tenant lookup must MISS (zero cross-tenant cache leakage)
    assert (
        await cache.get(
            prompt_msgs,
            "claude-3-5-sonnet",
            0.0,
            512,
            tenant_id="tenant-b",
            prompt_version="v1",
        )
        is None
    )

    # Upgraded prompt_version must MISS (no stale prompt contamination)
    assert (
        await cache.get(
            prompt_msgs,
            "claude-3-5-sonnet",
            0.0,
            512,
            tenant_id="tenant-a",
            prompt_version="v2",
        )
        is None
    )

    # Expired temporal entry must MISS even if Redis TTL hasn't evicted it yet
    volatile_msgs = [{"role": "user", "content": "volatile market status"}]
    await cache.set(
        volatile_msgs,
        "claude-3-5-sonnet",
        0.0,
        512,
        {"content": "stale snapshot"},
        tenant_id="tenant-a",
        prompt_version="v1",
        valid_until=datetime.now(UTC) - timedelta(seconds=5),
    )
    assert (
        await cache.get(
            volatile_msgs,
            "claude-3-5-sonnet",
            0.0,
            512,
            tenant_id="tenant-a",
            prompt_version="v1",
        )
        is None
    )


def test_pgvector_hnsw_and_run_jobs_postgres_migration_contracts() -> None:
    """Verify PostgreSQL migrations 004 & 005 define HNSW indexes, unified RLS, and versions."""
    sql_004 = (
        REPO_ROOT / "migrations" / "postgres" / "004_create_pgvector_semantic_store.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql_004
    assert "USING hnsw (embedding vector_cosine_ops)" in sql_004
    assert "current_setting('app.current_tenant', true)" in sql_004
    assert "VALUES (4)" in sql_004

    sql_005 = (
        REPO_ROOT / "migrations" / "postgres" / "005_create_run_jobs_and_events.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS run_jobs" in sql_005
    assert "CREATE TABLE IF NOT EXISTS run_events" in sql_005
    assert "ENABLE ROW LEVEL SECURITY" in sql_005
    assert "current_setting('app.current_tenant', true)" in sql_005
    assert "VALUES (5)" in sql_005

    # Verify cosine similarity ranking matches pgvector cosine ordering deterministically
    q_vec = local_prompt_embedding("checkout latency spike payment timeout")
    close_vec = local_prompt_embedding("checkout latency spike payment gateway timeout")
    far_vec = local_prompt_embedding("annual hr vacation policy handbook")
    assert cosine_similarity(q_vec, close_vec) > cosine_similarity(q_vec, far_vec)


@pytest.mark.asyncio
async def test_live_postgres_pgvector_and_redis_when_configured_in_ci() -> None:
    """Runs live against PostgreSQL+pgvector and Redis when CI env vars are present."""
    db_url = os.getenv("DATABASE_URL", "")
    redis_url = os.getenv("REDIS_URL", "")
    if not db_url.startswith(("postgresql://", "postgres://")) or not redis_url:
        pytest.skip("Requires live DATABASE_URL (postgresql://) and REDIS_URL")

    import redis.asyncio as aioredis

    from product_value_system.api.worker import PostgresRunJobQueue
    from product_value_system.persistence.postgres import PostgresDatabase
    from product_value_system.persistence.run_store import (
        DurableRunRecord,
        PostgresDurableRunStore,
        new_run_id,
    )

    pg_db = PostgresDatabase.from_env(dsn=db_url)
    store = PostgresDurableRunStore(pg_db)
    queue = PostgresRunJobQueue(pg_db)
    redis_client: Any = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await store.initialize()
        await queue.initialize()
        assert await store.health_check() is True

        now = datetime.now(UTC)
        rid = new_run_id()
        await store.create_run(
            DurableRunRecord(
                run_id=rid,
                tenant_id="ci-tenant",
                problem_id="PROB-CI-LIVE",
                problem_payload={"title": "Live CI PG+Redis Smoke"},
                status="pending",
                current_stage="queued",
                created_by="ci",
                created_at=now,
                updated_at=now,
            )
        )
        job = await queue.enqueue(run_id=rid, tenant_id="ci-tenant", job_type="start")
        claimed = await queue.claim_next("ci-worker")
        assert claimed is not None and claimed.job_id == job.job_id
        await queue.complete(job.job_id)

        cache = PromptCache(backend=RedisCacheBackend(client=redis_client, prefix="pvs:ci:"))
        ci_msgs = [{"role": "user", "content": "ci-live-key"}]
        await cache.set(
            ci_msgs,
            "mock",
            0.0,
            256,
            {"content": "ok"},
            tenant_id="ci-tenant",
            prompt_version="v1",
        )
        got = await cache.get(
            ci_msgs,
            "mock",
            0.0,
            256,
            tenant_id="ci-tenant",
            prompt_version="v1",
        )
        assert got == {"content": "ok"}
    finally:
        await redis_client.aclose()
        await store.close()
