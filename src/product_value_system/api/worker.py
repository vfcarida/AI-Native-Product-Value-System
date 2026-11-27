"""
Durable Asynchronous Run Execution Queue, Background Worker & Real-Time SSE
Event Stream (v2.6 — Control Plane Scale & Streaming).

Provides:
- ``RunJobQueue`` protocol with ``SQLiteRunJobQueue`` (WAL + lease reclaim) and
  ``PostgresRunJobQueue`` (atomic ``FOR UPDATE SKIP LOCKED`` claim + retries +
  dead-letter queue).
- ``RunEventBus`` combining durable ``run_events`` persistence with live
  per-run ``asyncio.Queue`` fan-out for standard Server-Sent Events (SSE)
  including ``Last-Event-ID`` replay without dropped events.
- ``DurableRunWorker`` executing queued Value Stream runs end-to-end, pausing
  at ``decision_gate`` for HITL approval, and automatically resuming upon API
  or Slack approval.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from product_value_system.persistence.run_store import (
    ApprovalRecord,
    DurableRunRecord,
    DurableRunStore,
    new_approval_id,
)

logger = logging.getLogger(__name__)

TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {"run.completed", "run.failed", "run.cancelled"}
)

StageExecutor = Callable[[DurableRunRecord, str], Awaitable[dict[str, Any]]]


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def new_job_id() -> str:
    return f"job-{uuid4().hex[:12]}"


@dataclass(frozen=True)
class RunEvent:
    """Single immutable lifecycle event emitted for a tenant-scoped run."""

    seq: int
    run_id: str
    tenant_id: str
    event_type: str
    stage_name: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "event_type": self.event_type,
            "stage_name": self.stage_name,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
        }

    def format_sse(self) -> str:
        """Render event in W3C Server-Sent Events wire format."""
        data_json = json.dumps(self.to_dict(), separators=(",", ":"))
        return f"id: {self.seq}\nevent: {self.event_type}\ndata: {data_json}\n\n"


@dataclass
class RunJob:
    """Durable background execution unit for starting or resuming a Value Stream run."""

    job_id: str
    run_id: str
    tenant_id: str
    job_type: str  # "start" | "resume"
    status: str  # "queued" | "leased" | "completed" | "failed" | "dead_letter"
    payload: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    max_attempts: int = 3
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    last_error: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "job_type": self.job_type,
            "status": self.status,
            "payload": self.payload,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "worker_id": self.worker_id,
            "lease_expires_at": (
                self.lease_expires_at.isoformat() if self.lease_expires_at else None
            ),
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


@runtime_checkable
class RunJobQueue(Protocol):
    """Durable queue & event log contract shared by SQLite and PostgreSQL backends."""

    async def initialize(self) -> None: ...

    async def enqueue(
        self,
        *,
        run_id: str,
        tenant_id: str,
        job_type: str = "start",
        payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
    ) -> RunJob: ...

    async def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 60.0,
    ) -> RunJob | None: ...

    async def complete(self, job_id: str) -> None: ...

    async def fail(self, job_id: str, error: str) -> RunJob | None: ...

    async def append_event(
        self,
        *,
        run_id: str,
        tenant_id: str,
        event_type: str,
        stage_name: str = "",
        payload: dict[str, Any] | None = None,
    ) -> RunEvent: ...

    async def fetch_events(
        self,
        run_id: str,
        *,
        tenant_id: str,
        after_seq: int = 0,
        limit: int = 500,
    ) -> list[RunEvent]: ...

    async def queue_stats(self) -> dict[str, int]: ...

    async def close(self) -> None: ...


class SQLiteRunJobQueue:
    """Durable WAL-mode SQLite job queue & SSE event log for single-node/dev deployments."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SQLiteRunJobQueue is not initialized; call initialize() first")
        return self._conn

    async def initialize(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS run_jobs (
                    job_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    job_type TEXT NOT NULL CHECK (job_type IN ('start', 'resume')),
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'leased', 'completed', 'failed', 'dead_letter')
                    ),
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    worker_id TEXT,
                    lease_expires_at TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_run_jobs_claim
                    ON run_jobs(status, lease_expires_at, created_at);
                CREATE TABLE IF NOT EXISTS run_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    stage_name TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_run_events_run_tenant_seq
                    ON run_events(run_id, tenant_id, seq);
                """
            )
            conn.commit()
            self._conn = conn

    @staticmethod
    def _row_to_job(row: Any) -> RunJob:
        raw_payload = row["payload_json"]
        payload = raw_payload if isinstance(raw_payload, dict) else json.loads(str(raw_payload))
        created = _dt(row["created_at"]) or datetime.now(UTC)
        updated = _dt(row["updated_at"]) or created
        return RunJob(
            job_id=str(row["job_id"]),
            run_id=str(row["run_id"]),
            tenant_id=str(row["tenant_id"]),
            job_type=str(row["job_type"]),
            status=str(row["status"]),
            payload=dict(payload),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            worker_id=str(row["worker_id"]) if row["worker_id"] is not None else None,
            lease_expires_at=_dt(row["lease_expires_at"]),
            last_error=str(row["last_error"] or ""),
            created_at=created,
            updated_at=updated,
            completed_at=_dt(row["completed_at"]),
        )

    async def enqueue(
        self,
        *,
        run_id: str,
        tenant_id: str,
        job_type: str = "start",
        payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
    ) -> RunJob:
        if job_type not in {"start", "resume"}:
            raise ValueError(f"Unsupported job_type: {job_type!r}")
        now = datetime.now(UTC)
        job = RunJob(
            job_id=new_job_id(),
            run_id=run_id,
            tenant_id=tenant_id,
            job_type=job_type,
            status="queued",
            payload=dict(payload or {}),
            attempts=0,
            max_attempts=max(1, int(max_attempts)),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            conn = self._require_conn()
            conn.execute(
                """
                INSERT INTO run_jobs (
                    job_id, run_id, tenant_id, job_type, status, payload_json,
                    attempts, max_attempts, worker_id, lease_expires_at,
                    last_error, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.run_id,
                    job.tenant_id,
                    job.job_type,
                    job.status,
                    json.dumps(job.payload),
                    job.attempts,
                    job.max_attempts,
                    None,
                    None,
                    "",
                    job.created_at.isoformat(timespec="microseconds"),
                    job.updated_at.isoformat(timespec="microseconds"),
                    None,
                ),
            )
            conn.commit()
        return job

    async def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 60.0,
    ) -> RunJob | None:
        now = datetime.now(UTC)
        now_iso = now.isoformat(timespec="microseconds")
        lease_iso = (now + timedelta(seconds=max(1.0, lease_seconds))).isoformat(
            timespec="microseconds"
        )
        with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                """
                SELECT * FROM run_jobs
                WHERE (
                    status = 'queued'
                    OR (
                        status = 'leased'
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at <= ?
                    )
                ) AND attempts < max_attempts
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now_iso,),
            ).fetchone()
            if row is None:
                return None
            cur = conn.execute(
                """
                UPDATE run_jobs
                SET status = 'leased', worker_id = ?, lease_expires_at = ?,
                    attempts = attempts + 1, updated_at = ?
                WHERE job_id = ? AND (
                    status = 'queued'
                    OR (
                        status = 'leased'
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at <= ?
                    )
                )
                """,
                (worker_id, lease_iso, now_iso, row["job_id"], now_iso),
            )
            conn.commit()
            if cur.rowcount != 1:
                return None
            claimed = conn.execute(
                "SELECT * FROM run_jobs WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
            return self._row_to_job(claimed) if claimed is not None else None

    async def complete(self, job_id: str) -> None:
        now_iso = datetime.now(UTC).isoformat(timespec="microseconds")
        with self._lock:
            conn = self._require_conn()
            conn.execute(
                """
                UPDATE run_jobs
                SET status = 'completed', lease_expires_at = NULL,
                    updated_at = ?, completed_at = ?
                WHERE job_id = ?
                """,
                (now_iso, now_iso, job_id),
            )
            conn.commit()

    async def fail(self, job_id: str, error: str) -> RunJob | None:
        now_iso = datetime.now(UTC).isoformat(timespec="microseconds")
        with self._lock:
            conn = self._require_conn()
            row = conn.execute("SELECT * FROM run_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            attempts = int(row["attempts"])
            max_attempts = int(row["max_attempts"])
            next_status = "dead_letter" if attempts >= max_attempts else "queued"
            conn.execute(
                """
                UPDATE run_jobs
                SET status = ?, lease_expires_at = NULL, last_error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (next_status, error[:2000], now_iso, job_id),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM run_jobs WHERE job_id = ?", (job_id,)).fetchone()
            return self._row_to_job(updated) if updated is not None else None

    async def append_event(
        self,
        *,
        run_id: str,
        tenant_id: str,
        event_type: str,
        stage_name: str = "",
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        now = datetime.now(UTC)
        payload_dict = dict(payload or {})
        with self._lock:
            conn = self._require_conn()
            cur = conn.execute(
                """
                INSERT INTO run_events (
                    run_id, tenant_id, event_type, stage_name, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    tenant_id,
                    event_type,
                    stage_name,
                    json.dumps(payload_dict),
                    now.isoformat(timespec="microseconds"),
                ),
            )
            conn.commit()
            seq = int(cur.lastrowid or 0)
        return RunEvent(
            seq=seq,
            run_id=run_id,
            tenant_id=tenant_id,
            event_type=event_type,
            stage_name=stage_name,
            payload=payload_dict,
            created_at=now,
        )

    async def fetch_events(
        self,
        run_id: str,
        *,
        tenant_id: str,
        after_seq: int = 0,
        limit: int = 500,
    ) -> list[RunEvent]:
        capped = max(1, min(limit, 2000))
        with self._lock:
            conn = self._require_conn()
            rows = conn.execute(
                """
                SELECT seq, run_id, tenant_id, event_type, stage_name, payload_json, created_at
                FROM run_events
                WHERE run_id = ? AND tenant_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (run_id, tenant_id, max(0, int(after_seq)), capped),
            ).fetchall()
        return [
            RunEvent(
                seq=int(r["seq"]),
                run_id=str(r["run_id"]),
                tenant_id=str(r["tenant_id"]),
                event_type=str(r["event_type"]),
                stage_name=str(r["stage_name"] or ""),
                payload=dict(json.loads(str(r["payload_json"] or "{}"))),
                created_at=_dt(r["created_at"]) or datetime.now(UTC),
            )
            for r in rows
        ]

    async def queue_stats(self) -> dict[str, int]:
        with self._lock:
            conn = self._require_conn()
            rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM run_jobs GROUP BY status"
            ).fetchall()
        stats = {"queued": 0, "leased": 0, "completed": 0, "failed": 0, "dead_letter": 0}
        for r in rows:
            stats[str(r["status"])] = int(r["cnt"])
        return stats

    async def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


class PostgresRunJobQueue:
    """
    Multi-replica PostgreSQL durable queue using atomic ``FOR UPDATE SKIP LOCKED``
    claims and durable ``run_events`` SSE log backed by ``PostgresDatabase``.
    """

    def __init__(self, database: Any) -> None:
        self._db = database

    async def initialize(self) -> None:
        if not self._db.is_connected:
            await self._db.connect()
        await self._db.migrate()

    @staticmethod
    def _row_to_job(row: Any) -> RunJob:
        raw_payload = row["payload_json"]
        payload = (
            dict(raw_payload)
            if isinstance(raw_payload, dict)
            else dict(json.loads(str(raw_payload or "{}")))
        )
        created = _dt(row["created_at"]) or datetime.now(UTC)
        updated = _dt(row["updated_at"]) or created
        return RunJob(
            job_id=str(row["job_id"]),
            run_id=str(row["run_id"]),
            tenant_id=str(row["tenant_id"]),
            job_type=str(row["job_type"]),
            status=str(row["status"]),
            payload=payload,
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            worker_id=str(row["worker_id"]) if row["worker_id"] is not None else None,
            lease_expires_at=_dt(row["lease_expires_at"]),
            last_error=str(row["last_error"] or ""),
            created_at=created,
            updated_at=updated,
            completed_at=_dt(row["completed_at"]),
        )

    async def enqueue(
        self,
        *,
        run_id: str,
        tenant_id: str,
        job_type: str = "start",
        payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
    ) -> RunJob:
        if job_type not in {"start", "resume"}:
            raise ValueError(f"Unsupported job_type: {job_type!r}")
        now = datetime.now(UTC)
        job = RunJob(
            job_id=new_job_id(),
            run_id=run_id,
            tenant_id=tenant_id,
            job_type=job_type,
            status="queued",
            payload=dict(payload or {}),
            attempts=0,
            max_attempts=max(1, int(max_attempts)),
            created_at=now,
            updated_at=now,
        )
        await self._db.execute(
            """
            INSERT INTO run_jobs (
                job_id, run_id, tenant_id, job_type, status, payload_json,
                attempts, max_attempts, worker_id, lease_expires_at,
                last_error, created_at, updated_at, completed_at
            ) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,$11,$12,$13,$14)
            """,
            job.job_id,
            job.run_id,
            job.tenant_id,
            job.job_type,
            job.status,
            json.dumps(job.payload),
            job.attempts,
            job.max_attempts,
            None,
            None,
            "",
            job.created_at,
            job.updated_at,
            None,
        )
        return job

    async def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 60.0,
    ) -> RunJob | None:
        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=max(1.0, lease_seconds))
        row = await self._db.fetchrow(
            """
            UPDATE run_jobs
            SET status = 'leased', worker_id = $1, lease_expires_at = $2,
                attempts = attempts + 1, updated_at = $3
            WHERE job_id = (
                SELECT job_id FROM run_jobs
                WHERE (
                    status = 'queued'
                    OR (
                        status = 'leased'
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at <= $3
                    )
                ) AND attempts < max_attempts
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING job_id, run_id, tenant_id, job_type, status, payload_json,
                      attempts, max_attempts, worker_id, lease_expires_at,
                      last_error, created_at, updated_at, completed_at
            """,
            worker_id,
            lease_until,
            now,
        )
        return None if row is None else self._row_to_job(row)

    async def complete(self, job_id: str) -> None:
        now = datetime.now(UTC)
        await self._db.execute(
            """
            UPDATE run_jobs
            SET status = 'completed', lease_expires_at = NULL,
                updated_at = $1, completed_at = $1
            WHERE job_id = $2
            """,
            now,
            job_id,
        )

    async def fail(self, job_id: str, error: str) -> RunJob | None:
        now = datetime.now(UTC)
        row = await self._db.fetchrow(
            """
            UPDATE run_jobs
            SET status = CASE WHEN attempts >= max_attempts THEN 'dead_letter' ELSE 'queued' END,
                lease_expires_at = NULL,
                last_error = $1,
                updated_at = $2
            WHERE job_id = $3
            RETURNING job_id, run_id, tenant_id, job_type, status, payload_json,
                      attempts, max_attempts, worker_id, lease_expires_at,
                      last_error, created_at, updated_at, completed_at
            """,
            error[:2000],
            now,
            job_id,
        )
        return None if row is None else self._row_to_job(row)

    async def append_event(
        self,
        *,
        run_id: str,
        tenant_id: str,
        event_type: str,
        stage_name: str = "",
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        now = datetime.now(UTC)
        payload_dict = dict(payload or {})
        row = await self._db.fetchrow(
            """
            INSERT INTO run_events (
                run_id, tenant_id, event_type, stage_name, payload_json, created_at
            ) VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            RETURNING seq, run_id, tenant_id, event_type, stage_name, payload_json, created_at
            """,
            run_id,
            tenant_id,
            event_type,
            stage_name,
            json.dumps(payload_dict),
            now,
        )
        assert row is not None
        return RunEvent(
            seq=int(row["seq"]),
            run_id=str(row["run_id"]),
            tenant_id=str(row["tenant_id"]),
            event_type=str(row["event_type"]),
            stage_name=str(row["stage_name"] or ""),
            payload=payload_dict,
            created_at=_dt(row["created_at"]) or now,
        )

    async def fetch_events(
        self,
        run_id: str,
        *,
        tenant_id: str,
        after_seq: int = 0,
        limit: int = 500,
    ) -> list[RunEvent]:
        capped = max(1, min(limit, 2000))
        rows = await self._db.fetch(
            """
            SELECT seq, run_id, tenant_id, event_type, stage_name, payload_json, created_at
            FROM run_events
            WHERE run_id = $1 AND tenant_id = $2 AND seq > $3
            ORDER BY seq ASC
            LIMIT $4
            """,
            run_id,
            tenant_id,
            max(0, int(after_seq)),
            capped,
        )
        out: list[RunEvent] = []
        for r in rows:
            raw_p = r["payload_json"]
            p = dict(raw_p) if isinstance(raw_p, dict) else dict(json.loads(str(raw_p or "{}")))
            out.append(
                RunEvent(
                    seq=int(r["seq"]),
                    run_id=str(r["run_id"]),
                    tenant_id=str(r["tenant_id"]),
                    event_type=str(r["event_type"]),
                    stage_name=str(r["stage_name"] or ""),
                    payload=p,
                    created_at=_dt(r["created_at"]) or datetime.now(UTC),
                )
            )
        return out

    async def queue_stats(self) -> dict[str, int]:
        rows = await self._db.fetch(
            "SELECT status, COUNT(*) AS cnt FROM run_jobs GROUP BY status"
        )
        stats = {"queued": 0, "leased": 0, "completed": 0, "failed": 0, "dead_letter": 0}
        for r in rows:
            stats[str(r["status"])] = int(r["cnt"])
        return stats

    async def close(self) -> None:
        return None


class RunEventBus:
    """
    Combines durable ``RunJobQueue`` event persistence with local
    ``asyncio.Queue`` fan-out, optional Redis Pub/Sub cross-replica broadcast,
    and durable DB tail polling fallback for multi-pod Kubernetes SSE streaming.
    """

    def __init__(
        self,
        job_queue: RunJobQueue | None = None,
        *,
        redis_client: Any | None = None,
        channel_prefix: str = "pvs:run_events:",
        poll_interval_seconds: float = 0.25,
    ) -> None:
        self._queue = job_queue
        self._redis: Any | None = redis_client
        self._channel_prefix = channel_prefix
        self._poll_interval_seconds = max(0.02, float(poll_interval_seconds))
        self._subscribers: dict[tuple[str, str], list[asyncio.Queue[RunEvent]]] = {}
        self._memory_events: dict[tuple[str, str], list[RunEvent]] = {}
        self._mem_seq = 0

    def set_redis_client(self, redis_client: Any | None) -> None:
        """Attach or replace the async Redis client used for cross-pod Pub/Sub fan-out."""
        self._redis = redis_client

    def _channel_name(self, tenant_id: str, run_id: str) -> str:
        return f"{self._channel_prefix}{tenant_id}:{run_id}"

    @staticmethod
    def _event_from_payload(raw_data: Any) -> RunEvent | None:
        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8", errors="replace")
        if isinstance(raw_data, str):
            try:
                parsed = json.loads(raw_data)
            except json.JSONDecodeError:
                return None
        elif isinstance(raw_data, dict):
            parsed = raw_data
        else:
            return None
        if not isinstance(parsed, dict) or "seq" not in parsed or "run_id" not in parsed:
            return None
        return RunEvent(
            seq=int(parsed["seq"]),
            run_id=str(parsed["run_id"]),
            tenant_id=str(parsed.get("tenant_id", "default")),
            event_type=str(parsed.get("event_type", "")),
            stage_name=str(parsed.get("stage_name", "")),
            payload=dict(parsed.get("payload") or {}),
            created_at=_dt(parsed.get("created_at")) or datetime.now(UTC),
        )

    async def publish(
        self,
        *,
        run_id: str,
        tenant_id: str,
        event_type: str,
        stage_name: str = "",
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        if self._queue is not None:
            event = await self._queue.append_event(
                run_id=run_id,
                tenant_id=tenant_id,
                event_type=event_type,
                stage_name=stage_name,
                payload=payload,
            )
        else:
            self._mem_seq += 1
            event = RunEvent(
                seq=self._mem_seq,
                run_id=run_id,
                tenant_id=tenant_id,
                event_type=event_type,
                stage_name=stage_name,
                payload=dict(payload or {}),
            )
            self._memory_events.setdefault((tenant_id, run_id), []).append(event)

        for sub_q in list(self._subscribers.get((tenant_id, run_id), ())):
            try:
                sub_q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Dropping SSE event for slow subscriber on run %s", run_id)

        if self._redis is not None and callable(getattr(self._redis, "publish", None)):
            try:
                serialized = json.dumps(event.to_dict(), separators=(",", ":"))
                await self._redis.publish(self._channel_name(tenant_id, run_id), serialized)
            except Exception as exc:
                logger.warning(
                    "Redis Pub/Sub publish degraded for run %s (%s); DB tail fallback active",
                    run_id,
                    exc,
                )
        return event

    async def list_events(
        self,
        run_id: str,
        *,
        tenant_id: str,
        after_seq: int = 0,
        limit: int = 500,
    ) -> list[RunEvent]:
        if self._queue is not None:
            return await self._queue.fetch_events(
                run_id, tenant_id=tenant_id, after_seq=after_seq, limit=limit
            )
        items = self._memory_events.get((tenant_id, run_id), [])
        return [e for e in items if e.seq > after_seq][:limit]

    async def stream_sse(
        self,
        run_id: str,
        *,
        tenant_id: str,
        after_seq: int = 0,
        follow: bool = True,
        heartbeat_seconds: float = 15.0,
        idle_timeout_seconds: float = 30.0,
    ) -> AsyncIterator[str]:
        """Yield W3C SSE frames replaying history and streaming multi-replica live updates."""
        key = (tenant_id, run_id)
        sub_q: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=256)
        pubsub: Any | None = None
        redis_task: asyncio.Task[None] | None = None
        channel = self._channel_name(tenant_id, run_id)

        if follow:
            self._subscribers.setdefault(key, []).append(sub_q)
            if self._redis is not None and callable(getattr(self._redis, "pubsub", None)):
                try:
                    pubsub = self._redis.pubsub()
                    await pubsub.subscribe(channel)

                    async def _relay_redis_messages(ps: Any) -> None:
                        try:
                            async for msg in ps.listen():
                                if not isinstance(msg, dict):
                                    continue
                                if msg.get("type") != "message":
                                    continue
                                evt = self._event_from_payload(msg.get("data"))
                                if (
                                    evt is not None
                                    and evt.run_id == run_id
                                    and evt.tenant_id == tenant_id
                                ):
                                    try:
                                        sub_q.put_nowait(evt)
                                    except asyncio.QueueFull:
                                        logger.warning(
                                            "Dropping Redis SSE event for slow subscriber %s",
                                            run_id,
                                        )
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.warning(
                                "Redis Pub/Sub listener degraded for %s (%s); DB tail active",
                                run_id,
                                exc,
                            )

                    redis_task = asyncio.create_task(
                        _relay_redis_messages(pubsub),
                        name=f"pvs-sse-redis-{run_id[:12]}",
                    )
                except Exception as exc:
                    logger.warning(
                        "Redis Pub/Sub subscribe failed for run %s (%s); using DB tail fallback",
                        run_id,
                        exc,
                    )
                    pubsub = None
                    redis_task = None

        last_seq = max(0, int(after_seq))
        try:
            history = await self.list_events(run_id, tenant_id=tenant_id, after_seq=last_seq)
            for evt in history:
                last_seq = max(last_seq, evt.seq)
                yield evt.format_sse()
                if evt.event_type in TERMINAL_EVENT_TYPES:
                    return

            if not follow:
                return

            loop = asyncio.get_running_loop()
            hb_interval = max(0.05, float(heartbeat_seconds))
            idle_window = max(0.2, float(idle_timeout_seconds))
            deadline = loop.time() + idle_window
            next_heartbeat = loop.time() + hb_interval

            while True:
                now = loop.time()
                remaining = deadline - now
                if remaining <= 0:
                    return
                hb_wait = max(0.01, next_heartbeat - now)
                if self._queue is not None:
                    wait_for = min(self._poll_interval_seconds, hb_wait, remaining)
                else:
                    wait_for = min(hb_wait, remaining)

                try:
                    evt = await asyncio.wait_for(sub_q.get(), timeout=wait_for)
                except TimeoutError:
                    if self._queue is not None:
                        tail = await self.list_events(
                            run_id, tenant_id=tenant_id, after_seq=last_seq
                        )
                        emitted = False
                        for tail_evt in tail:
                            if tail_evt.seq <= last_seq:
                                continue
                            last_seq = tail_evt.seq
                            emitted = True
                            yield tail_evt.format_sse()
                            deadline = loop.time() + idle_window
                            next_heartbeat = loop.time() + hb_interval
                            if tail_evt.event_type in TERMINAL_EVENT_TYPES:
                                return
                        if emitted:
                            continue
                    if loop.time() >= next_heartbeat:
                        yield ": heartbeat\n\n"
                        next_heartbeat = loop.time() + hb_interval
                    continue

                if evt.seq <= last_seq:
                    continue
                last_seq = evt.seq
                yield evt.format_sse()
                deadline = loop.time() + idle_window
                next_heartbeat = loop.time() + hb_interval
                if evt.event_type in TERMINAL_EVENT_TYPES:
                    return
        finally:
            if redis_task is not None:
                redis_task.cancel()
                try:
                    await redis_task
                except (asyncio.CancelledError, Exception):
                    pass
            if pubsub is not None:
                try:
                    unsub = getattr(pubsub, "unsubscribe", None)
                    if callable(unsub):
                        res = unsub(channel)
                        if hasattr(res, "__await__"):
                            await res
                except Exception:
                    pass
                try:
                    close_ps = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
                    if callable(close_ps):
                        res = close_ps()
                        if hasattr(res, "__await__"):
                            await res
                except Exception:
                    pass
            if follow:
                subs = self._subscribers.get(key)
                if subs and sub_q in subs:
                    subs.remove(sub_q)
                    if not subs:
                        self._subscribers.pop(key, None)


class DurableRunWorker:
    """
    Background worker consuming ``RunJobQueue`` jobs and driving tenant-scoped
    ``DurableRunRecord`` state transitions, HITL pauses/resumes, and SSE events.
    """

    def __init__(
        self,
        *,
        run_store: DurableRunStore,
        job_queue: RunJobQueue,
        event_bus: RunEventBus,
        stage_executor: StageExecutor | None = None,
        worker_id: str | None = None,
        poll_interval_seconds: float = 0.05,
        lease_seconds: float = 60.0,
    ) -> None:
        self.run_store = run_store
        self.job_queue = job_queue
        self.event_bus = event_bus
        self._stage_executor = stage_executor or self._default_stage_executor
        self.worker_id = worker_id or f"pvs-worker-{uuid4().hex[:8]}"
        self.poll_interval_seconds = max(0.01, float(poll_interval_seconds))
        self.lease_seconds = max(5.0, float(lease_seconds))
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def notify(self) -> None:
        """Wake the background loop immediately after a new job is enqueued."""
        self._wake_event.set()

    def start(self) -> None:
        """Start the background worker loop inside the active event loop."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name=self.worker_id)

    async def stop(self) -> None:
        """Gracefully stop and drain the background worker loop."""
        self._stop_event.set()
        self._wake_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except TimeoutError:
                self._task.cancel()
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                processed = await self.process_once()
                if processed:
                    continue
            except Exception as exc:
                logger.exception("DurableRunWorker loop error: %s", exc)
            self._wake_event.clear()
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(), timeout=self.poll_interval_seconds
                )
            except TimeoutError:
                continue

    async def process_once(self) -> bool:
        """Claim and execute at most one pending job. Returns True if a job ran."""
        job = await self.job_queue.claim_next(
            self.worker_id, lease_seconds=self.lease_seconds
        )
        if job is None:
            return False

        try:
            await self._execute_job(job)
            await self.job_queue.complete(job.job_id)
        except Exception as exc:
            logger.error("Job %s for run %s failed: %s", job.job_id, job.run_id, exc)
            updated_job = await self.job_queue.fail(job.job_id, str(exc))
            if updated_job is not None and updated_job.status == "dead_letter":
                rec = await self.run_store.get_run(job.run_id, tenant_id=job.tenant_id)
                if rec is not None and rec.status not in {"completed", "cancelled"}:
                    rec.status = "failed"
                    rec.completed_at = datetime.now(UTC)
                    rec.stages[rec.current_stage] = {
                        "status": "failed",
                        "error": str(exc),
                        "failed_at": rec.completed_at.isoformat(),
                    }
                    await self.run_store.update_run(rec)
                await self.event_bus.publish(
                    run_id=job.run_id,
                    tenant_id=job.tenant_id,
                    event_type="run.failed",
                    stage_name=job.job_type,
                    payload={"job_id": job.job_id, "error": str(exc), "dead_letter": True},
                )
        return True

    async def _execute_job(self, job: RunJob) -> None:
        rec = await self.run_store.get_run(job.run_id, tenant_id=job.tenant_id)
        if rec is None:
            logger.warning("Skipping orphaned job %s: run %s not found", job.job_id, job.run_id)
            return
        if rec.status in {"completed", "cancelled"}:
            return

        require_approval = bool(job.payload.get("require_human_approval", True))
        if job.job_type == "start":
            await self._run_pre_gate_stages(rec, require_approval=require_approval)
        elif job.job_type == "resume":
            await self._run_post_gate_stages(rec)

    async def _run_pre_gate_stages(
        self,
        rec: DurableRunRecord,
        *,
        require_approval: bool,
    ) -> None:
        rec.status = "running"
        rec.current_stage = "discovery"
        await self.run_store.update_run(rec)
        await self.event_bus.publish(
            run_id=rec.run_id,
            tenant_id=rec.tenant_id,
            event_type="run.started",
            stage_name="discovery",
            payload={"problem_id": rec.problem_id, "worker_id": self.worker_id},
        )

        for stage in ("discovery", "specification", "parallel_review"):
            await self._run_single_stage(rec, stage)

        now = datetime.now(UTC)
        if require_approval:
            rec.status = "waiting_approval"
            rec.current_stage = "decision_gate"
            rec.stages["decision_gate"] = {
                "status": "waiting_approval",
                "requested_at": now.isoformat(),
            }
            await self.run_store.update_run(rec)
            approval = ApprovalRecord(
                approval_id=new_approval_id(),
                run_id=rec.run_id,
                tenant_id=rec.tenant_id,
                stage_name="decision_gate",
                status="pending",
                requested_at=now,
            )
            await self.run_store.create_approval(approval)
            await self.event_bus.publish(
                run_id=rec.run_id,
                tenant_id=rec.tenant_id,
                event_type="hitl.approval_requested",
                stage_name="decision_gate",
                payload={
                    "approval_id": approval.approval_id,
                    "stage_name": "decision_gate",
                },
            )
            return

        rec.stages["decision_gate"] = {
            "status": "skipped",
            "completed_at": now.isoformat(),
        }
        await self.run_store.update_run(rec)
        await self._run_post_gate_stages(rec)

    async def _run_post_gate_stages(self, rec: DurableRunRecord) -> None:
        rec.status = "running"
        await self.run_store.update_run(rec)

        for stage in ("build", "measurement"):
            stage_out = await self._run_single_stage(rec, stage)
            if stage == "build" and stage_out.get("diffs"):
                await self.event_bus.publish(
                    run_id=rec.run_id,
                    tenant_id=rec.tenant_id,
                    event_type="build.diff",
                    stage_name="build",
                    payload={"diffs": stage_out["diffs"]},
                )

        now = datetime.now(UTC)
        rec.status = "completed"
        rec.current_stage = "completed"
        rec.completed_at = now
        await self.run_store.update_run(rec)
        await self.event_bus.publish(
            run_id=rec.run_id,
            tenant_id=rec.tenant_id,
            event_type="run.completed",
            stage_name="completed",
            payload={
                "total_cost_usd": round(rec.total_cost_usd, 6),
                "artifacts": sorted(rec.artifacts.keys()),
            },
        )

    async def _run_single_stage(
        self,
        rec: DurableRunRecord,
        stage_name: str,
    ) -> dict[str, Any]:
        started_at = datetime.now(UTC)
        rec.current_stage = stage_name
        rec.stages[stage_name] = {
            "status": "running",
            "started_at": started_at.isoformat(),
        }
        await self.run_store.update_run(rec)
        await self.event_bus.publish(
            run_id=rec.run_id,
            tenant_id=rec.tenant_id,
            event_type="stage.started",
            stage_name=stage_name,
            payload={"stage": stage_name},
        )

        output = await self._stage_executor(rec, stage_name)
        completed_at = datetime.now(UTC)
        stage_cost = float(output.get("cost_usd", 0.0))
        rec.total_cost_usd = round(rec.total_cost_usd + stage_cost, 6)
        rec.stages[stage_name] = {
            "status": "completed",
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "cost_usd": stage_cost,
            "summary": str(output.get("summary", "")),
        }
        if "artifact" in output:
            rec.artifacts[stage_name] = output["artifact"]
        await self.run_store.update_run(rec)

        await self.event_bus.publish(
            run_id=rec.run_id,
            tenant_id=rec.tenant_id,
            event_type="stage.completed",
            stage_name=stage_name,
            payload={
                "stage": stage_name,
                "cost_usd": stage_cost,
                "summary": str(output.get("summary", "")),
            },
        )
        return output

    @staticmethod
    async def _default_stage_executor(
        rec: DurableRunRecord,
        stage_name: str,
    ) -> dict[str, Any]:
        """Deterministic zero-cost governed stage executor (override for live LLM runs)."""
        title = str(rec.problem_payload.get("title", rec.problem_id))
        if stage_name == "discovery":
            return {
                "cost_usd": 0.002,
                "summary": f"Synthesized opportunity tree for '{title}'",
                "artifact": {
                    "problem_id": rec.problem_id,
                    "primary_metric": "conversion_rate",
                    "confidence": 0.85,
                },
            }
        if stage_name == "specification":
            return {
                "cost_usd": 0.003,
                "summary": f"Generated executable specification for '{title}'",
                "artifact": {
                    "spec_id": f"SPEC-{rec.problem_id}",
                    "acceptance_criteria_count": 3,
                },
            }
        if stage_name == "parallel_review":
            return {
                "cost_usd": 0.004,
                "summary": "4-perspective review passed (0 critical findings)",
                "artifact": {
                    "perspectives": ["security", "architecture", "performance", "maintainability"],
                    "critical_findings": 0,
                },
            }
        if stage_name == "build":
            diff_summary = [
                {
                    "path": f"src/{rec.problem_id.lower().replace('-', '_')}.py",
                    "change_type": "created",
                    "lines_added": 24,
                    "lines_removed": 0,
                }
            ]
            return {
                "cost_usd": 0.005,
                "summary": "Sandboxed ReAct build & pytest suite passed",
                "diffs": diff_summary,
                "artifact": {"tests_passed": True, "diffs": diff_summary},
            }
        return {
            "cost_usd": 0.001,
            "summary": "Outcome measurement baseline & SLO guardrails registered",
            "artifact": {"baseline_registered": True, "slo_status": "healthy"},
        }


class LiveAgentStageExecutor:
    """
    Governed live-LLM ``StageExecutor`` bridging ``DurableRunWorker`` to
    ``Harness`` + ``ModelAdapter`` with per-run budget ceilings, OWASP
    guardrails, and stage-specific prompts.

    Uses lazy imports inside execution methods so importing ASGI control plane
    modules never pulls optional ML/scientific libraries at boot time.
    """

    _STAGE_CONFIG: dict[str, tuple[str, str]] = {
        "discovery": (
            "discovery",
            (
                "Analyze the product problem, surface evidence gaps, and propose a "
                "prioritized opportunity tree with a primary KPI."
            ),
        ),
        "specification": (
            "specification",
            (
                "Write a concise executable specification with functional requirements, "
                "acceptance criteria, and edge cases."
            ),
        ),
        "parallel_review": (
            "review",
            (
                "Review the specification across security, architecture, performance, "
                "and maintainability perspectives and list critical findings."
            ),
        ),
        "build": (
            "build",
            (
                "Produce a minimal, well-tested implementation plan and concise diff "
                "summary with verification steps."
            ),
        ),
        "measurement": (
            "measurement",
            (
                "Define post-launch outcome measurement metrics, SLO guardrails, "
                "and rollback triggers."
            ),
        ),
    }

    def __init__(
        self,
        *,
        provider: str = "auto",
        model: str | None = None,
        model_adapter: Any | None = None,
        max_cost_usd: float = 1.0,
        max_tokens_per_stage: int = 800,
        policy_engine: Any | None = None,
    ) -> None:
        self.provider = provider.strip().lower() or "auto"
        self.model = model
        self._explicit_adapter = model_adapter
        self._cached_adapter: Any | None = None
        self.max_cost_usd = max(0.01, float(max_cost_usd))
        self.max_tokens_per_stage = max(64, int(max_tokens_per_stage))
        self._policy_engine = policy_engine

    def _resolve_adapter(self) -> Any:
        if self._explicit_adapter is not None:
            return self._explicit_adapter
        if self._cached_adapter is not None:
            return self._cached_adapter

        import os

        from product_value_system.adapters import create_adapter

        provider = self.provider
        if provider == "auto":
            if os.environ.get("ANTHROPIC_API_KEY"):
                provider = "anthropic"
            elif os.environ.get("OPENAI_API_KEY"):
                provider = "openai"
            elif os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
                provider = "gemini"
            elif os.environ.get("AZURE_OPENAI_API_KEY"):
                provider = "azure_openai"
            else:
                logger.warning(
                    "PVS_WORKER_EXECUTOR=live with provider=auto found no LLM API keys; "
                    "falling back to MockModelAdapter"
                )
                provider = "mock"

        self._cached_adapter = create_adapter(provider, model=self.model)
        return self._cached_adapter

    async def __call__(
        self,
        rec: DurableRunRecord,
        stage_name: str,
    ) -> dict[str, Any]:
        from decimal import Decimal

        from product_value_system.adapters.model_adapter import Message, Role
        from product_value_system.harness.budget import Budget
        from product_value_system.harness.context import ExecutionContext
        from product_value_system.harness.harness import Harness

        agent_name, instruction = self._STAGE_CONFIG.get(
            stage_name,
            ("orchestrator", f"Execute governed Value Stream stage '{stage_name}'."),
        )
        adapter = self._resolve_adapter()
        ctx = ExecutionContext(run_id=rec.run_id, agent_name=agent_name)
        budget = Budget(max_cost_usd=Decimal(str(self.max_cost_usd)))
        harness = Harness(
            context=ctx,
            budget=budget,
            model_adapter=adapter,
            policy_engine=self._policy_engine,
        )

        prior_artifacts = json.dumps(rec.artifacts, sort_keys=True, default=str)[:2000]
        problem_json = json.dumps(rec.problem_payload, sort_keys=True, default=str)[:2000]
        messages = [
            Message(
                role=Role.SYSTEM,
                content=(
                    f"You are the Product Value System {agent_name} agent operating under "
                    f"governed Cedar policy and FinOps budget constraints. {instruction}"
                ),
            ),
            Message(
                role=Role.USER,
                content=(
                    f"Problem ID: {rec.problem_id}\n"
                    f"Stage: {stage_name}\n"
                    f"Problem Payload: {problem_json}\n"
                    f"Prior Stage Artifacts: {prior_artifacts}"
                ),
            ),
        ]

        response = await harness.invoke_model(
            messages,
            max_tokens=self.max_tokens_per_stage,
            task_type=stage_name,
        )

        content = str(response.content or "").strip()
        summary = content.splitlines()[0][:240] if content else f"Completed stage '{stage_name}'"
        cost_usd = round(float(harness.budget.current_cost_usd), 6)
        total_tokens = int(response.usage.total_tokens) if response.usage is not None else 0
        artifact: dict[str, Any] = {
            "executor": "live",
            "agent": agent_name,
            "model": response.model or self.model or "unknown",
            "total_tokens": total_tokens,
            "content": content,
        }
        result: dict[str, Any] = {
            "cost_usd": cost_usd,
            "summary": summary,
            "artifact": artifact,
        }
        if stage_name == "build":
            diffs = [
                {
                    "path": f"src/{rec.problem_id.lower().replace('-', '_')}.py",
                    "change_type": "planned",
                    "summary": summary,
                }
            ]
            result["diffs"] = diffs
            artifact["diffs"] = diffs
        return result
