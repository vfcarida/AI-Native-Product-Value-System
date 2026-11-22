"""Performance regression & optimization tests (Round 9)."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

from product_value_system.caching.cache_backends import CachedResponse, SQLiteCacheBackend
from product_value_system.persistence.database import Database
from product_value_system.policy.engine import PolicyEngine

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_policy_engine_mtime_cache_accelerates_instantiation_and_invalidates(
    tmp_path: Path,
) -> None:
    """Repeated PolicyEngine instantiations hit mtime cache and reload on file modification."""
    PolicyEngine.clear_policy_cache()

    policy_file = tmp_path / "agent_authorization.cedar"
    policy_file.write_text("// v1 policy\n", encoding="utf-8")

    # Cold load misses cache
    e1 = PolicyEngine(policies_dir=tmp_path)
    assert e1._cache_hit is False
    assert "v1 policy" in e1._policies_text

    # 100 warm instantiations hit cache with sub-millisecond average overhead
    t0 = time.perf_counter()
    for _ in range(100):
        e_warm = PolicyEngine(policies_dir=tmp_path)
        assert e_warm._cache_hit is True
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, f"100 cached PolicyEngine loads took {elapsed:.3f}s"

    # Modifying policy file & bumping mtime automatically invalidates cache
    policy_file.write_text("// v2 updated policy\n", encoding="utf-8")
    stat = policy_file.stat()
    os.utime(policy_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    e2 = PolicyEngine(policies_dir=tmp_path)
    assert e2._cache_hit is False
    assert "v2 updated policy" in e2._policies_text


async def test_sqlite_wal_mode_enabled_on_database_and_cache_backend(tmp_path: Path) -> None:
    """File-backed Database and SQLiteCacheBackend enable WAL + NORMAL synchronous mode."""
    db_path = tmp_path / "pvs_wal.db"
    db = Database(db_path, migrations_dir=REPO_ROOT / "migrations")
    db.connect()
    assert db._connection is not None
    mode = str(db._connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    sync_mode = int(db._connection.execute("PRAGMA synchronous").fetchone()[0])
    assert mode == "wal"
    assert sync_mode == 1  # NORMAL == 1
    db.close()

    cache_db = tmp_path / "cache_wal.db"
    backend = SQLiteCacheBackend(cache_db, max_entries=100)
    entry = CachedResponse(
        response={"content": "cached-ok"},
        cache_key="k-wal-1",
        prompt_hash="h-1",
        model="gpt-4o-mini",
        temperature=0.0,
        cached_at=datetime.now(UTC),
        ttl_seconds=3600,
    )
    await backend.set("k-wal-1", entry)
    loaded = await backend.get("k-wal-1")
    assert loaded is not None
    assert loaded.response["content"] == "cached-ok"

    with backend._connect() as conn:
        cache_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    assert cache_mode == "wal"
