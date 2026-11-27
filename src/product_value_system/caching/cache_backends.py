"""
Cache backends for LLM prompt caching: In-Memory (LRU), SQLite (persistent),
Redis (distributed), and Tiered L1/L2 (EVO-025).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CachedResponse:
    """Cached LLM response with TTL, temporal validity, prompt version, and semantic embedding."""

    response: dict[str, Any]
    cache_key: str
    prompt_hash: str
    model: str
    temperature: float
    cached_at: datetime
    ttl_seconds: int
    hit_count: int = 0
    embedding: list[float] | None = None
    prompt_text: str = ""
    prompt_version: str = ""
    tenant_id: str = ""
    valid_until: datetime | None = None

    def is_expired(self, *, now: datetime | None = None) -> bool:
        ref = now or datetime.now(UTC)
        if self.valid_until is not None:
            vu_utc = (
                self.valid_until.astimezone(UTC)
                if self.valid_until.tzinfo is not None
                else self.valid_until.replace(tzinfo=UTC)
            )
            if ref > vu_utc:
                return True
        cached_utc = (
            self.cached_at.astimezone(UTC)
            if self.cached_at.tzinfo is not None
            else self.cached_at.replace(tzinfo=UTC)
        )
        return (ref - cached_utc).total_seconds() > self.ttl_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "response": self.response,
            "cache_key": self.cache_key,
            "prompt_hash": self.prompt_hash,
            "model": self.model,
            "temperature": self.temperature,
            "cached_at": self.cached_at.isoformat(),
            "ttl_seconds": self.ttl_seconds,
            "hit_count": self.hit_count,
            "embedding": self.embedding,
            "prompt_text": self.prompt_text,
            "prompt_version": self.prompt_version,
            "tenant_id": self.tenant_id,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CachedResponse:
        raw_vu = data.get("valid_until")
        return cls(
            response=dict(data["response"]),
            cache_key=str(data["cache_key"]),
            prompt_hash=str(data["prompt_hash"]),
            model=str(data["model"]),
            temperature=float(data["temperature"]),
            cached_at=datetime.fromisoformat(str(data["cached_at"])),
            ttl_seconds=int(data["ttl_seconds"]),
            hit_count=int(data.get("hit_count", 0)),
            embedding=data.get("embedding"),
            prompt_text=str(data.get("prompt_text", "")),
            prompt_version=str(data.get("prompt_version", "")),
            tenant_id=str(data.get("tenant_id", "")),
            valid_until=datetime.fromisoformat(str(raw_vu)) if raw_vu else None,
        )


class CacheBackend(ABC):
    """Abstract async cache storage backend."""

    @abstractmethod
    async def get(self, key: str) -> CachedResponse | None:
        """Retrieve a cached response by key."""

    @abstractmethod
    async def set(self, key: str, value: CachedResponse) -> None:
        """Store or update a cached response."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete an entry by key."""

    @abstractmethod
    async def clear(self) -> None:
        """Remove all entries from cache."""

    @abstractmethod
    async def list_entries(self, model: str | None = None) -> list[CachedResponse]:
        """List stored entries, optionally filtered by model identifier."""

    @abstractmethod
    async def delete_by_model(self, model: str) -> int:
        """Invalidate all cached entries matching ``model`` and return count removed."""

    @abstractmethod
    async def size(self) -> int:
        """Return total number of entries currently in cache."""


class MemoryCacheBackend(CacheBackend):
    """Async-safe in-memory LRU cache backend."""

    def __init__(self, max_entries: int = 10000) -> None:
        self.max_entries = max(1, int(max_entries))
        self._cache: OrderedDict[str, CachedResponse] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> CachedResponse | None:
        async with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                self._cache.move_to_end(key)
            return entry

    async def set(self, key: str, value: CachedResponse) -> None:
        async with self._lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._cache.clear()

    async def list_entries(self, model: str | None = None) -> list[CachedResponse]:
        async with self._lock:
            entries = list(self._cache.values())
        if model is not None:
            return [e for e in entries if e.model == model]
        return entries

    async def delete_by_model(self, model: str) -> int:
        async with self._lock:
            keys = [k for k, v in self._cache.items() if v.model == model]
            for k in keys:
                self._cache.pop(k, None)
            return len(keys)

    async def size(self) -> int:
        async with self._lock:
            return len(self._cache)


class RedisCacheBackend(CacheBackend):
    """Redis-backed distributed cache (supports injected async client for testing)."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        *,
        prefix: str = "llm_cache:",
        client: Any | None = None,
    ) -> None:
        self.prefix = prefix
        if client is not None:
            self.client = client
        else:
            import redis.asyncio as aioredis

            self.client = aioredis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                decode_responses=True,
            )

    def _prefixed(self, key: str) -> str:
        return f"{self.prefix}{key}"

    async def get(self, key: str) -> CachedResponse | None:
        raw = await self.client.get(self._prefixed(key))
        if not raw:
            return None
        return CachedResponse.from_dict(json.loads(raw))

    async def set(self, key: str, value: CachedResponse) -> None:
        await self.client.set(
            self._prefixed(key),
            json.dumps(value.to_dict()),
            ex=max(1, int(value.ttl_seconds)),
        )

    async def delete(self, key: str) -> None:
        await self.client.delete(self._prefixed(key))

    async def clear(self) -> None:
        keys = await self.client.keys(f"{self.prefix}*")
        if keys:
            await self.client.delete(*keys)

    async def list_entries(self, model: str | None = None) -> list[CachedResponse]:
        keys = await self.client.keys(f"{self.prefix}*")
        entries: list[CachedResponse] = []
        for full_key in keys:
            raw = await self.client.get(full_key)
            if not raw:
                continue
            entry = CachedResponse.from_dict(json.loads(raw))
            if model is None or entry.model == model:
                entries.append(entry)
        return entries

    async def delete_by_model(self, model: str) -> int:
        entries = await self.list_entries(model=model)
        for entry in entries:
            await self.delete(entry.cache_key)
        return len(entries)

    async def size(self) -> int:
        keys = await self.client.keys(f"{self.prefix}*")
        return len(keys)


class SQLiteCacheBackend(CacheBackend):
    """Persistent local SQLite cache backend with LRU eviction (uses aiosqlite or stdlib)."""

    def __init__(
        self,
        db_path: str | Path = ".cache/llm_cache.db",
        *,
        max_entries: int = 10000,
    ) -> None:
        self.db_path = Path(db_path)
        self.max_entries = max(1, int(max_entries))
        self._initialized = False
        self._lock = asyncio.Lock()
        self._conn_lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._pending_touches: dict[str, str] = {}

    def _open_connection(self) -> sqlite3.Connection:
        """Open and tune a persistent WAL-mode SQLite connection without leaking handles."""
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA temp_store = MEMORY")
            conn.execute("PRAGMA cache_size = -16384")
        except BaseException:
            conn.close()
            raise
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = self._open_connection()
        return self._conn

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield the persistent WAL-tuned SQLite connection under a reentrant lock."""
        with self._conn_lock:
            yield self._get_conn()

    def close(self) -> None:
        """Flush deferred LRU touches and deterministically close the SQLite connection."""
        with self._conn_lock:
            conn = self._conn
            self._conn = None
            if conn is not None:
                try:
                    self._flush_touches_locked(conn)
                    conn.commit()
                except sqlite3.DatabaseError:
                    pass
                finally:
                    conn.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    @staticmethod
    def _next_monotonic_iso(candidate: datetime, floor_iso: str | None) -> str:
        """Return a fixed-width microsecond ISO timestamp strictly greater than ``floor_iso``."""
        if floor_iso:
            try:
                floor_dt = datetime.fromisoformat(floor_iso)
                if candidate <= floor_dt:
                    candidate = floor_dt + timedelta(microseconds=1)
            except ValueError:
                pass
        return candidate.isoformat(timespec="microseconds")

    def _flush_touches_locked(self, conn: sqlite3.Connection) -> str | None:
        row = conn.execute("SELECT MAX(last_accessed) FROM llm_cache").fetchone()
        max_iso: str | None = str(row[0]) if row and row[0] else None
        if not self._pending_touches:
            return max_iso
        items: list[tuple[str, str]] = []
        for k, raw_ts in self._pending_touches.items():
            try:
                cand = datetime.fromisoformat(raw_ts)
            except ValueError:
                cand = datetime.now(UTC)
            max_iso = self._next_monotonic_iso(cand, max_iso)
            items.append((max_iso, k))
        self._pending_touches.clear()
        conn.executemany(
            "UPDATE llm_cache SET last_accessed = ?, hit_count = hit_count + 1 WHERE cache_key = ?",
            items,
        )
        return max_iso

    def _quarantine_corrupt_db(self, reason: Exception) -> None:
        """Quarantine a corrupt SQLite cache file (and WAL/SHM sidecars) for clean rebuild."""
        quarantine = self.db_path.with_suffix(self.db_path.suffix + ".corrupt")
        logger.warning(
            "SQLite cache corruption detected at %s (%s); quarantining to %s and rebuilding.",
            self.db_path,
            reason,
            quarantine,
        )
        if self.db_path.exists():
            os.replace(self.db_path, quarantine)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.db_path}{suffix}")
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except OSError:
                    pass

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_cache (
                cache_key TEXT PRIMARY KEY,
                response TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                temperature REAL NOT NULL,
                cached_at TEXT NOT NULL,
                ttl_seconds INTEGER NOT NULL,
                hit_count INTEGER NOT NULL,
                embedding TEXT,
                prompt_text TEXT NOT NULL DEFAULT '',
                last_accessed TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cached_at ON llm_cache(cached_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_model ON llm_cache(model)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_last_accessed ON llm_cache(last_accessed)"
        )
        conn.commit()

    def _init_sync(self) -> None:
        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                check_row = conn.execute("PRAGMA quick_check").fetchone()
                if check_row and str(check_row[0]).lower() != "ok":
                    raise sqlite3.DatabaseError(f"PRAGMA quick_check failed: {check_row[0]}")
                self._create_schema(conn)
        except sqlite3.DatabaseError as exc:
            self.close()
            self._quarantine_corrupt_db(exc)
            with self._connect() as conn:
                self._create_schema(conn)
        self._initialized = True

    @staticmethod
    def _row_to_entry(row: tuple[Any, ...]) -> CachedResponse:
        return CachedResponse(
            cache_key=str(row[0]),
            response=json.loads(row[1]),
            prompt_hash=str(row[2]),
            model=str(row[3]),
            temperature=float(row[4]),
            cached_at=datetime.fromisoformat(str(row[5])),
            ttl_seconds=int(row[6]),
            hit_count=int(row[7]),
            embedding=json.loads(row[8]) if row[8] else None,
            prompt_text=str(row[9] or ""),
        )

    def _get_sync(self, key: str) -> CachedResponse | None:
        self._init_sync()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT cache_key, response, prompt_hash, model, temperature,
                       cached_at, ttl_seconds, hit_count, embedding, prompt_text
                FROM llm_cache WHERE cache_key = ?
                """,
                (key,),
            ).fetchone()
            if row is None:
                return None
            # Defer LRU timestamp write to next write/eviction transaction (zero write on read)
            self._pending_touches[key] = datetime.now(UTC).isoformat(timespec="microseconds")
            return self._row_to_entry(row)

    def _set_sync(self, key: str, value: CachedResponse) -> None:
        self._init_sync()
        with self._connect() as conn:
            max_iso = self._flush_touches_locked(conn)
            self._pending_touches.pop(key, None)
            now_iso = self._next_monotonic_iso(datetime.now(UTC), max_iso)
            conn.execute(
                """
                INSERT OR REPLACE INTO llm_cache (
                    cache_key, response, prompt_hash, model, temperature,
                    cached_at, ttl_seconds, hit_count, embedding, prompt_text, last_accessed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    json.dumps(value.response),
                    value.prompt_hash,
                    value.model,
                    value.temperature,
                    value.cached_at.isoformat(),
                    value.ttl_seconds,
                    value.hit_count,
                    json.dumps(value.embedding) if value.embedding is not None else None,
                    value.prompt_text,
                    now_iso,
                ),
            )
            count = int(conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0])
            if count > self.max_entries:
                excess = count - self.max_entries
                conn.execute(
                    """
                    DELETE FROM llm_cache
                    WHERE cache_key IN (
                        SELECT cache_key FROM llm_cache
                        ORDER BY last_accessed ASC
                        LIMIT ?
                    )
                    """,
                    (excess,),
                )
            conn.commit()

    def _delete_sync(self, key: str) -> None:
        self._init_sync()
        with self._connect() as conn:
            conn.execute("DELETE FROM llm_cache WHERE cache_key = ?", (key,))
            conn.commit()

    def _clear_sync(self) -> None:
        self._init_sync()
        with self._connect() as conn:
            conn.execute("DELETE FROM llm_cache")
            conn.commit()

    def _list_sync(self, model: str | None) -> list[CachedResponse]:
        self._init_sync()
        with self._connect() as conn:
            if model is not None:
                rows = conn.execute(
                    """
                    SELECT cache_key, response, prompt_hash, model, temperature,
                           cached_at, ttl_seconds, hit_count, embedding, prompt_text
                    FROM llm_cache WHERE model = ?
                    """,
                    (model,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT cache_key, response, prompt_hash, model, temperature,
                           cached_at, ttl_seconds, hit_count, embedding, prompt_text
                    FROM llm_cache
                    """
                ).fetchall()
            return [self._row_to_entry(r) for r in rows]

    def _delete_by_model_sync(self, model: str) -> int:
        self._init_sync()
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM llm_cache WHERE model = ?", (model,))
            conn.commit()
            return int(cur.rowcount)

    def _size_sync(self) -> int:
        self._init_sync()
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0])

    async def get(self, key: str) -> CachedResponse | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, key)

    async def set(self, key: str, value: CachedResponse) -> None:
        async with self._lock:
            await asyncio.to_thread(self._set_sync, key, value)

    async def delete(self, key: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete_sync, key)

    async def clear(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._clear_sync)

    async def list_entries(self, model: str | None = None) -> list[CachedResponse]:
        async with self._lock:
            return await asyncio.to_thread(self._list_sync, model)

    async def delete_by_model(self, model: str) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._delete_by_model_sync, model)

    async def size(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._size_sync)


class TieredCacheBackend(CacheBackend):
    """Multi-level L1 (in-memory) + L2 (SQLite/Redis) cache with automatic L1 backfill."""

    def __init__(self, l1: CacheBackend, l2: CacheBackend) -> None:
        self.l1 = l1
        self.l2 = l2

    async def get(self, key: str) -> CachedResponse | None:
        entry = await self.l1.get(key)
        if entry is not None:
            return entry
        entry = await self.l2.get(key)
        if entry is not None:
            await self.l1.set(key, entry)
        return entry

    async def set(self, key: str, value: CachedResponse) -> None:
        await self.l1.set(key, value)
        await self.l2.set(key, value)

    async def delete(self, key: str) -> None:
        await self.l1.delete(key)
        await self.l2.delete(key)

    async def clear(self) -> None:
        await self.l1.clear()
        await self.l2.clear()

    async def list_entries(self, model: str | None = None) -> list[CachedResponse]:
        return await self.l2.list_entries(model=model)

    async def delete_by_model(self, model: str) -> int:
        await self.l1.delete_by_model(model)
        return await self.l2.delete_by_model(model)

    async def size(self) -> int:
        return await self.l2.size()
