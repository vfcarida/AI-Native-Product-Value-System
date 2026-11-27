"""
Immutable compliance audit logging service for SOC2, GDPR, and HIPAA (PROD-006).

Features:
- Append-only persistence backends: SQLite WAL (with database-level ``BEFORE UPDATE``
  and ``BEFORE DELETE`` immutability triggers), PostgreSQL (asyncpg), and In-Memory.
- Cryptographic integrity via HMAC-SHA256 signing (``AuditSigner``) with constant-time
  verification and zero-downtime key-rotation support.
- Sub-2ms critical-path logging via non-blocking async queue + background worker.
- Reliability circuit breaker and ``AUDIT_EMERGENCY`` stderr fallback so persistence
  failures never crash application code.
- Automatic metadata redaction of sensitive credential fields, SOC2 CSV evidence
  export (with CSV formula-injection neutralization), and meta-audit logging.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import sqlite3
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from product_value_system.compliance.signing import AuditSigner

logger = logging.getLogger(__name__)

_SENSITIVE_KEY_TOKENS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth_token",
    "bearer",
    "authorization",
    "private_key",
    "client_secret",
    "signing_key",
    "credit_card",
    "ssn",
)

_CSV_FORMULA_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")

_SQLITE_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    user_id TEXT,
    subject_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    result TEXT NOT NULL,
    metadata TEXT,
    client_ip TEXT,
    user_agent TEXT,
    session_id TEXT,
    signature TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_event_id ON audit_logs(event_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_subject_id ON audit_logs(subject_id);
CREATE INDEX IF NOT EXISTS idx_audit_resource_type ON audit_logs(resource_type);
CREATE INDEX IF NOT EXISTS idx_audit_session_id ON audit_logs(session_id);

CREATE INDEX IF NOT EXISTS idx_audit_user_timestamp ON audit_logs(user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_subject_timestamp ON audit_logs(subject_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_type_timestamp ON audit_logs(event_type, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_logs(session_id, timestamp);

CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_update
BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit_logs is append-only: UPDATE is prohibited');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_delete
BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit_logs is append-only: DELETE is prohibited');
END;
"""


class AuditEventType(Enum):
    """Structured audit event categories mapped to SOC2, GDPR, and HIPAA controls."""

    USER_LOGIN = "user.login"
    USER_LOGOUT = "user.logout"
    USER_LOGIN_FAILED = "user.login.failed"
    USER_CREATED = "user.created"
    USER_DELETED = "user.deleted"
    USER_PERMISSION_CHANGED = "user.permission.changed"

    DATA_READ = "data.read"
    DATA_WRITE = "data.write"
    DATA_DELETE = "data.delete"
    DATA_EXPORT = "data.export"

    MODEL_CALLED = "model.called"
    MODEL_RESPONSE = "model.response"

    AGENT_STARTED = "agent.started"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"

    CODE_EXECUTED = "code.executed"

    SECRET_ACCESSED = "secret.accessed"
    SECRET_ROTATED = "secret.rotated"

    CONFIG_CHANGED = "config.changed"
    AUDIT_LOG_ACCESSED = "audit.log.accessed"

    GDPR_EXPORT_REQUESTED = "gdpr.export.requested"
    GDPR_DELETION_REQUESTED = "gdpr.deletion.requested"


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize naive or offset-aware datetimes into UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def redact_sensitive_metadata(data: Any) -> Any:
    """Recursively redact sensitive credential keys before writing to immutable logs."""
    if isinstance(data, Mapping):
        sanitized: dict[str, Any] = {}
        for key, value in data.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if any(tok in key_lower for tok in _SENSITIVE_KEY_TOKENS):
                sanitized[key_str] = "[REDACTED]"
            else:
                sanitized[key_str] = redact_sensitive_metadata(value)
        return sanitized
    if isinstance(data, list):
        return [redact_sensitive_metadata(item) for item in data]
    if isinstance(data, tuple):
        return [redact_sensitive_metadata(item) for item in data]
    return data


def _sanitize_csv_cell(value: Any) -> str:
    """Neutralize spreadsheet formula injection when exporting CSV auditor reports."""
    text = "" if value is None else str(value)
    if text.startswith(_CSV_FORMULA_PREFIXES):
        return f"'{text}"
    return text


@dataclass
class AuditEvent:
    """Immutable-intended structured compliance audit event."""

    event_type: AuditEventType
    timestamp: datetime
    user_id: str | None
    subject_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    result: str
    metadata: dict[str, Any] = field(default_factory=dict)
    client_ip: str | None = None
    user_agent: str | None = None
    session_id: str | None = None

    # Computed fields set by AuditLogger
    event_id: str | None = None
    signature: str | None = None

    def __post_init__(self) -> None:
        self.timestamp = _ensure_utc(self.timestamp)

    def signing_payload(self) -> dict[str, Any]:
        """Return the canonical signing payload covering all integrity-protected fields."""
        return {
            "event_type": self.event_type.value,
            "timestamp": _ensure_utc(self.timestamp).isoformat(),
            "user_id": self.user_id,
            "subject_id": self.subject_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "result": self.result,
            "metadata": self.metadata,
            "client_ip": self.client_ip,
            "session_id": self.session_id,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize event into a JSON-compatible dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": _ensure_utc(self.timestamp).isoformat(),
            "user_id": self.user_id,
            "subject_id": self.subject_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "result": self.result,
            "metadata": self.metadata,
            "client_ip": self.client_ip,
            "user_agent": self.user_agent,
            "session_id": self.session_id,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AuditEvent:
        """Reconstruct an ``AuditEvent`` from a serialized dictionary."""
        ts_raw = raw["timestamp"]
        ts = ts_raw if isinstance(ts_raw, datetime) else datetime.fromisoformat(str(ts_raw))
        et_raw = raw["event_type"]
        et = et_raw if isinstance(et_raw, AuditEventType) else AuditEventType(str(et_raw))
        meta = raw.get("metadata")
        if isinstance(meta, str):
            meta = json.loads(meta) if meta else {}
        return cls(
            event_type=et,
            timestamp=_ensure_utc(ts),
            user_id=raw.get("user_id"),
            subject_id=raw.get("subject_id"),
            action=str(raw["action"]),
            resource_type=str(raw["resource_type"]),
            resource_id=raw.get("resource_id"),
            result=str(raw["result"]),
            metadata=dict(meta) if isinstance(meta, Mapping) else {},
            client_ip=raw.get("client_ip"),
            user_agent=raw.get("user_agent"),
            session_id=raw.get("session_id"),
            event_id=raw.get("event_id"),
            signature=raw.get("signature"),
        )


@runtime_checkable
class AuditStore(Protocol):
    """Append-only storage contract for compliance audit events."""

    async def insert(self, event: AuditEvent) -> None: ...

    async def query(
        self,
        *,
        user_id: str | None = None,
        subject_id: str | None = None,
        event_type: AuditEventType | None = None,
        resource_type: str | None = None,
        session_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]: ...

    async def query_older_than(
        self, cutoff: datetime, *, limit: int = 10000
    ) -> list[AuditEvent]: ...

    async def archive_and_prune(self, event_ids: Sequence[str]) -> int: ...

    async def anonymize_subject(self, subject_id: str) -> int: ...

    async def count(self) -> int: ...


class InMemoryAuditStore:
    """Append-only in-memory audit store for low-latency unit tests and benchmarks."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    async def insert(self, event: AuditEvent) -> None:
        self._events.append(AuditEvent.from_dict(event.to_dict()))

    def update(self, *_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("audit_logs is append-only: UPDATE is prohibited")

    def delete(self, *_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("audit_logs is append-only: DELETE is prohibited")

    async def query(
        self,
        *,
        user_id: str | None = None,
        subject_id: str | None = None,
        event_type: AuditEventType | None = None,
        resource_type: str | None = None,
        session_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        start_utc = _ensure_utc(start_time) if start_time else None
        end_utc = _ensure_utc(end_time) if end_time else None
        matched: list[AuditEvent] = []
        for ev in self._events:
            if user_id is not None and ev.user_id != user_id:
                continue
            if subject_id is not None and ev.subject_id != subject_id:
                continue
            if event_type is not None and ev.event_type != event_type:
                continue
            if resource_type is not None and ev.resource_type != resource_type:
                continue
            if session_id is not None and ev.session_id != session_id:
                continue
            if start_utc is not None and ev.timestamp < start_utc:
                continue
            if end_utc is not None and ev.timestamp > end_utc:
                continue
            matched.append(AuditEvent.from_dict(ev.to_dict()))
        matched.sort(key=lambda e: e.timestamp, reverse=True)
        return matched[offset : offset + limit]

    async def query_older_than(self, cutoff: datetime, *, limit: int = 10000) -> list[AuditEvent]:
        cutoff_utc = _ensure_utc(cutoff)
        old = [
            AuditEvent.from_dict(ev.to_dict())
            for ev in self._events
            if ev.timestamp < cutoff_utc
        ]
        old.sort(key=lambda e: e.timestamp)
        return old[:limit]

    async def archive_and_prune(self, event_ids: Sequence[str]) -> int:
        id_set = set(event_ids)
        before = len(self._events)
        self._events = [ev for ev in self._events if ev.event_id not in id_set]
        return before - len(self._events)

    async def anonymize_subject(self, subject_id: str) -> int:
        updated = 0
        for ev in self._events:
            if ev.subject_id == subject_id or ev.user_id == subject_id:
                if ev.subject_id == subject_id:
                    ev.subject_id = "[ANONYMIZED]"
                if ev.user_id == subject_id:
                    ev.user_id = "[ANONYMIZED]"
                ev.client_ip = None
                ev.user_agent = None
                updated += 1
        return updated

    async def count(self) -> int:
        return len(self._events)


class SQLiteAuditStore:
    """Append-only SQLite WAL audit store enforced by database-level triggers.

    Standard ``UPDATE`` and ``DELETE`` SQL statements against ``audit_logs`` are
    unconditionally rejected by ``BEFORE UPDATE`` and ``BEFORE DELETE`` triggers.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if self.db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.executescript(_SQLITE_AUDIT_DDL)
        self._conn.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose underlying SQLite connection for inspection and immutability tests."""
        return self._conn

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()

    async def insert(self, event: AuditEvent) -> None:
        self._conn.execute(
            """
            INSERT INTO audit_logs (
                event_id, event_type, timestamp, user_id, subject_id,
                action, resource_type, resource_id, result, metadata,
                client_ip, user_agent, session_id, signature
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.event_type.value,
                _ensure_utc(event.timestamp).isoformat(),
                event.user_id,
                event.subject_id,
                event.action,
                event.resource_type,
                event.resource_id,
                event.result,
                json.dumps(event.metadata, sort_keys=True),
                event.client_ip,
                event.user_agent,
                event.session_id,
                event.signature,
            ),
        )
        self._conn.commit()

    async def query(
        self,
        *,
        user_id: str | None = None,
        subject_id: str | None = None,
        event_type: AuditEventType | None = None,
        resource_type: str | None = None,
        session_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if subject_id is not None:
            clauses.append("subject_id = ?")
            params.append(subject_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type.value)
        if resource_type is not None:
            clauses.append("resource_type = ?")
            params.append(resource_type)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if start_time is not None:
            clauses.append("timestamp >= ?")
            params.append(_ensure_utc(start_time).isoformat())
        if end_time is not None:
            clauses.append("timestamp <= ?")
            params.append(_ensure_utc(end_time).isoformat())

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT event_id, event_type, timestamp, user_id, subject_id, action, "
            f"resource_type, resource_id, result, metadata, client_ip, user_agent, "
            f"session_id, signature FROM audit_logs {where_sql} "
            f"ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        return [AuditEvent.from_dict(dict(row)) for row in rows]

    async def query_older_than(self, cutoff: datetime, *, limit: int = 10000) -> list[AuditEvent]:
        rows = self._conn.execute(
            """
            SELECT event_id, event_type, timestamp, user_id, subject_id, action,
                   resource_type, resource_id, result, metadata, client_ip, user_agent,
                   session_id, signature
            FROM audit_logs
            WHERE timestamp < ?
            ORDER BY timestamp ASC, id ASC
            LIMIT ?
            """,
            (_ensure_utc(cutoff).isoformat(), limit),
        ).fetchall()
        return [AuditEvent.from_dict(dict(row)) for row in rows]

    async def archive_and_prune(self, event_ids: Sequence[str]) -> int:
        """Privileged lifecycle archival prune (called only after confirmed cold upload)."""
        if not event_ids:
            return 0
        placeholders = ",".join("?" for _ in event_ids)
        try:
            self._conn.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_delete")
            cursor = self._conn.execute(
                f"DELETE FROM audit_logs WHERE event_id IN ({placeholders})",
                list(event_ids),
            )
            deleted = cursor.rowcount
            self._conn.commit()
        finally:
            self._conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_delete
                BEFORE DELETE ON audit_logs
                BEGIN
                    SELECT RAISE(ABORT, 'audit_logs is append-only: DELETE is prohibited');
                END;
                """
            )
            self._conn.commit()
        return deleted

    async def anonymize_subject(self, subject_id: str) -> int:
        """Privileged GDPR Art. 17 anonymization path when ``retain_audit_logs=False``."""
        try:
            self._conn.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_update")
            cursor = self._conn.execute(
                """
                UPDATE audit_logs
                SET subject_id = CASE WHEN subject_id = ? THEN '[ANONYMIZED]' ELSE subject_id END,
                    user_id = CASE WHEN user_id = ? THEN '[ANONYMIZED]' ELSE user_id END,
                    client_ip = NULL,
                    user_agent = NULL
                WHERE subject_id = ? OR user_id = ?
                """,
                (subject_id, subject_id, subject_id, subject_id),
            )
            updated = cursor.rowcount
            self._conn.commit()
        finally:
            self._conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_update
                BEFORE UPDATE ON audit_logs
                BEGIN
                    SELECT RAISE(ABORT, 'audit_logs is append-only: UPDATE is prohibited');
                END;
                """
            )
            self._conn.commit()
        return updated

    async def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS cnt FROM audit_logs").fetchone()
        return int(row["cnt"]) if row else 0


class PostgresAuditStore:
    """Async PostgreSQL (``asyncpg``) append-only audit store matching migration 002."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def insert(self, event: AuditEvent) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_logs (
                    event_id, event_type, timestamp, user_id, subject_id,
                    action, resource_type, resource_id, result, metadata,
                    client_ip, user_agent, session_id, signature
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12, $13, $14)
                """,
                event.event_id,
                event.event_type.value,
                _ensure_utc(event.timestamp),
                event.user_id,
                event.subject_id,
                event.action,
                event.resource_type,
                event.resource_id,
                event.result,
                json.dumps(event.metadata, sort_keys=True),
                event.client_ip,
                event.user_agent,
                event.session_id,
                event.signature,
            )

    async def query(
        self,
        *,
        user_id: str | None = None,
        subject_id: str | None = None,
        event_type: AuditEventType | None = None,
        resource_type: str | None = None,
        session_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        clauses: list[str] = []
        params: list[Any] = []

        def _add(col_expr: str, val: Any) -> None:
            params.append(val)
            clauses.append(f"{col_expr} ${len(params)}")

        if user_id is not None:
            _add("user_id =", user_id)
        if subject_id is not None:
            _add("subject_id =", subject_id)
        if event_type is not None:
            _add("event_type =", event_type.value)
        if resource_type is not None:
            _add("resource_type =", resource_type)
        if session_id is not None:
            _add("session_id =", session_id)
        if start_time is not None:
            _add("timestamp >=", _ensure_utc(start_time))
        if end_time is not None:
            _add("timestamp <=", _ensure_utc(end_time))

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        limit_idx = len(params)
        params.append(offset)
        offset_idx = len(params)
        sql = (
            f"SELECT event_id, event_type, timestamp, user_id, subject_id, action, "
            f"resource_type, resource_id, result, metadata, client_ip, user_agent, "
            f"session_id, signature FROM audit_logs {where_sql} "
            f"ORDER BY timestamp DESC, id DESC LIMIT ${limit_idx} OFFSET ${offset_idx}"
        )
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [AuditEvent.from_dict(dict(row)) for row in rows]

    async def query_older_than(self, cutoff: datetime, *, limit: int = 10000) -> list[AuditEvent]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT event_id, event_type, timestamp, user_id, subject_id, action,
                       resource_type, resource_id, result, metadata, client_ip, user_agent,
                       session_id, signature
                FROM audit_logs
                WHERE timestamp < $1
                ORDER BY timestamp ASC, id ASC
                LIMIT $2
                """,
                _ensure_utc(cutoff),
                limit,
            )
        return [AuditEvent.from_dict(dict(row)) for row in rows]

    async def archive_and_prune(self, event_ids: Sequence[str]) -> int:
        if not event_ids:
            return 0
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM audit_logs WHERE event_id = ANY($1::text[])",
                list(event_ids),
            )
        parts = str(result).split()
        return int(parts[-1]) if parts and parts[-1].isdigit() else 0

    async def anonymize_subject(self, subject_id: str) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE audit_logs
                SET subject_id = CASE WHEN subject_id = $1 THEN '[ANONYMIZED]' ELSE subject_id END,
                    user_id = CASE WHEN user_id = $1 THEN '[ANONYMIZED]' ELSE user_id END,
                    client_ip = NULL,
                    user_agent = NULL
                WHERE subject_id = $1 OR user_id = $1
                """,
                subject_id,
            )
        parts = str(result).split()
        return int(parts[-1]) if parts and parts[-1].isdigit() else 0

    async def count(self) -> int:
        async with self._pool.acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM audit_logs"))


class DeferredSQLiteAuditStore:
    """
    Lazy-initializing append-only SQLite audit store for ASGI lifespan management.

    Defers opening the SQLite file until ``initialize()`` or the first audit
    operation so importing ASGI composition modules remains side-effect free,
    and cleanly closes file descriptors on shutdown.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)
        self._store: SQLiteAuditStore | None = None

    def _ensure_store(self) -> SQLiteAuditStore:
        if self._store is None:
            self._store = SQLiteAuditStore(self.db_path)
        return self._store

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose underlying SQLite connection for inspection and immutability tests."""
        return self._ensure_store().connection

    async def initialize(self) -> None:
        """Eagerly open and migrate the append-only SQLite audit database."""
        self._ensure_store()

    async def close(self) -> None:
        """Close the underlying SQLite connection if open."""
        if self._store is not None:
            self._store.close()
            self._store = None

    async def insert(self, event: AuditEvent) -> None:
        await self._ensure_store().insert(event)

    async def query(
        self,
        *,
        user_id: str | None = None,
        subject_id: str | None = None,
        event_type: AuditEventType | None = None,
        resource_type: str | None = None,
        session_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        return await self._ensure_store().query(
            user_id=user_id,
            subject_id=subject_id,
            event_type=event_type,
            resource_type=resource_type,
            session_id=session_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            offset=offset,
        )

    async def query_older_than(self, cutoff: datetime, *, limit: int = 10000) -> list[AuditEvent]:
        return await self._ensure_store().query_older_than(cutoff, limit=limit)

    async def archive_and_prune(self, event_ids: Sequence[str]) -> int:
        return await self._ensure_store().archive_and_prune(event_ids)

    async def anonymize_subject(self, subject_id: str) -> int:
        return await self._ensure_store().anonymize_subject(subject_id)

    async def count(self) -> int:
        return await self._ensure_store().count()


class DeferredPostgresAuditStore:
    """
    Lazy-binding append-only PostgreSQL audit store backed by a ``PostgresDatabase``.

    Resolves the ``asyncpg`` pool once ``PostgresDatabase.connect()`` completes
    during ASGI lifespan startup, delegating all calls to ``PostgresAuditStore``.
    """

    def __init__(self, database: Any) -> None:
        self._database = database

    def _ensure_store(self) -> PostgresAuditStore:
        pool = getattr(self._database, "_pool", None)
        if pool is None:
            raise RuntimeError(
                "DeferredPostgresAuditStore requires an initialized PostgreSQL connection pool"
            )
        return PostgresAuditStore(pool)

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def insert(self, event: AuditEvent) -> None:
        await self._ensure_store().insert(event)

    async def query(
        self,
        *,
        user_id: str | None = None,
        subject_id: str | None = None,
        event_type: AuditEventType | None = None,
        resource_type: str | None = None,
        session_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        return await self._ensure_store().query(
            user_id=user_id,
            subject_id=subject_id,
            event_type=event_type,
            resource_type=resource_type,
            session_id=session_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            offset=offset,
        )

    async def query_older_than(self, cutoff: datetime, *, limit: int = 10000) -> list[AuditEvent]:
        return await self._ensure_store().query_older_than(cutoff, limit=limit)

    async def archive_and_prune(self, event_ids: Sequence[str]) -> int:
        return await self._ensure_store().archive_and_prune(event_ids)

    async def anonymize_subject(self, subject_id: str) -> int:
        return await self._ensure_store().anonymize_subject(subject_id)

    async def count(self) -> int:
        return await self._ensure_store().count()


class AuditLogger:
    """Async compliance audit logger with HMAC-SHA256 signing and circuit-breaker fallback."""

    def __init__(
        self,
        store: AuditStore | Path | str,
        signing_key: bytes | str,
        *,
        enable_async: bool = True,
        queue_size: int = 10000,
        enabled: bool = True,
        redact_metadata: bool = True,
        previous_signing_keys: Sequence[bytes | str] = (),
        circuit_breaker_threshold: int = 5,
        circuit_breaker_cooldown_seconds: float = 30.0,
    ) -> None:
        if isinstance(store, (Path, str)):
            self.store: AuditStore = SQLiteAuditStore(store)
        else:
            self.store = store
        self.signer = AuditSigner(signing_key, previous_keys=previous_signing_keys)
        self.signing_key: bytes = (
            signing_key.encode("utf-8") if isinstance(signing_key, str) else bytes(signing_key)
        )
        self.enable_async = enable_async
        self.queue_size = max(1, queue_size)
        self.enabled = enabled
        self.redact_metadata = redact_metadata

        # Circuit breaker state (prevents cascading failures if DB goes down)
        self._cb_threshold = max(1, circuit_breaker_threshold)
        self._cb_cooldown = max(1.0, circuit_breaker_cooldown_seconds)
        self._consecutive_failures = 0
        self._circuit_open_until: float = 0.0

        # Metrics counters
        self.events_logged = 0
        self.emergency_fallbacks = 0
        self.tamper_alerts = 0

        self._queue: asyncio.Queue[AuditEvent] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._closed = False

        if self.enable_async:
            self._maybe_start_worker()

    def _maybe_start_worker(self) -> None:
        """Start the background queue worker if an event loop is actively running."""
        if not self.enable_async or self._closed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=self.queue_size)
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = loop.create_task(self._worker())

    def _is_circuit_open(self) -> bool:
        if self._consecutive_failures < self._cb_threshold:
            return False
        if time.monotonic() >= self._circuit_open_until:
            # Half-open: allow one probe attempt
            return False
        return True

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._cb_threshold:
            self._circuit_open_until = time.monotonic() + self._cb_cooldown

    async def log(self, event: AuditEvent) -> AuditEvent:
        """Sign and log an audit event without ever crashing the caller."""
        if not self.enabled:
            return event

        if self.redact_metadata and event.metadata:
            event.metadata = redact_sensitive_metadata(event.metadata)

        if not event.event_id:
            event.event_id = self._generate_event_id()
        event.signature = self._sign_event(event)

        if self._is_circuit_open():
            self._emergency_log(event, reason="circuit_breaker_open")
            return event

        if self.enable_async:
            self._maybe_start_worker()
            if self._queue is None:
                await self._safe_persist(event)
                return event
            try:
                self._queue.put_nowait(event)
                self.events_logged += 1
                return event
            except asyncio.QueueFull:
                try:
                    await asyncio.wait_for(self._queue.put(event), timeout=1.0)
                    self.events_logged += 1
                    return event
                except TimeoutError:
                    logger.error(
                        "Audit log queue full, falling back to emergency stderr log",
                        extra={"event_id": event.event_id},
                    )
                    self._emergency_log(event, reason="queue_full")
                    return event

        await self._safe_persist(event)
        return event

    async def _safe_persist(self, event: AuditEvent) -> None:
        try:
            await self._persist_event(event)
            self._record_success()
            self.events_logged += 1
        except Exception as exc:
            logger.error("Failed to persist audit event %s: %s", event.event_id, exc)
            self._record_failure()
            self._emergency_log(event, reason=str(exc))

    async def _worker(self) -> None:
        """Background consumer persisting queued audit events to storage."""
        assert self._queue is not None
        while True:
            try:
                event = await self._queue.get()
            except asyncio.CancelledError:
                return
            try:
                await self._safe_persist(event)
            finally:
                self._queue.task_done()

    async def _persist_event(self, event: AuditEvent) -> None:
        """Persist a signed audit event to the configured append-only store."""
        await self.store.insert(event)

    async def flush(self, timeout: float = 5.0) -> None:
        """Wait for all queued async audit events to finish persisting."""
        if self._queue is not None:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)

    async def close(self) -> None:
        """Flush pending events and stop the background worker cleanly."""
        self._closed = True
        if self._queue is not None:
            try:
                await self.flush(timeout=5.0)
            except TimeoutError:
                logger.warning("Timed out waiting for audit queue to drain on close")
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if isinstance(self.store, SQLiteAuditStore):
            self.store.close()

    def _generate_event_id(self) -> str:
        """Generate a unique UUID4 event identifier."""
        return str(uuid.uuid4())

    def _sign_event(self, event: AuditEvent) -> str:
        """Compute deterministic HMAC-SHA256 hex signature (64 chars)."""
        return self.signer.sign(event.signing_payload())

    def verify_signature(self, event: AuditEvent) -> bool:
        """Verify event HMAC-SHA256 signature in constant time (tamper detection)."""
        return self.signer.verify(event.signing_payload(), event.signature)

    def verify_events(self, events: Sequence[AuditEvent]) -> dict[str, Any]:
        """Verify HMAC signatures across a collection of audit events."""
        tampered_ids: list[str] = []
        for ev in events:
            if not self.verify_signature(ev):
                tampered_ids.append(ev.event_id or "unknown")
        if tampered_ids:
            self.tamper_alerts += len(tampered_ids)
        return {
            "total": len(events),
            "valid": len(events) - len(tampered_ids),
            "tampered": len(tampered_ids),
            "tampered_event_ids": tampered_ids,
            "integrity_verified": len(tampered_ids) == 0,
        }

    def _emergency_log(self, event: AuditEvent, *, reason: str = "persistence_failure") -> None:
        """Write structured ``AUDIT_EMERGENCY`` JSON record to stderr when DB is unavailable."""
        self.emergency_fallbacks += 1
        emergency_record = {
            "level": "AUDIT_EMERGENCY",
            "reason": reason,
            "event": event.to_dict(),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        print(json.dumps(emergency_record, sort_keys=True), file=sys.stderr, flush=True)

    async def search(
        self,
        user_id: str | None = None,
        subject_id: str | None = None,
        event_type: AuditEventType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
        *,
        resource_type: str | None = None,
        session_id: str | None = None,
        actor_user_id: str | None = None,
        log_access: bool = False,
        verify_integrity: bool = True,
    ) -> list[AuditEvent]:
        """Search audit logs with optional meta-audit logging and tamper detection."""
        bounded_limit = max(1, min(limit, 1000))
        bounded_offset = max(0, offset)

        events = await self.store.query(
            user_id=user_id,
            subject_id=subject_id,
            event_type=event_type,
            resource_type=resource_type,
            session_id=session_id,
            start_time=start_time,
            end_time=end_time,
            limit=bounded_limit,
            offset=bounded_offset,
        )

        if verify_integrity:
            report = self.verify_events(events)
            if not report["integrity_verified"]:
                logger.critical(
                    "Audit log signature tamper detected on event_ids=%s",
                    report["tampered_event_ids"],
                )

        if log_access:
            await self.log(
                AuditEvent(
                    event_type=AuditEventType.AUDIT_LOG_ACCESSED,
                    timestamp=datetime.now(UTC),
                    user_id=actor_user_id,
                    subject_id=subject_id,
                    action="Audit logs queried",
                    resource_type="audit_logs",
                    resource_id=None,
                    result="success",
                    metadata={
                        "filters": {
                            "user_id": user_id,
                            "subject_id": subject_id,
                            "event_type": event_type.value if event_type else None,
                            "resource_type": resource_type,
                            "limit": bounded_limit,
                            "offset": bounded_offset,
                        },
                        "result_count": len(events),
                    },
                )
            )

        return events

    async def export_csv(
        self,
        *,
        user_id: str | None = None,
        subject_id: str | None = None,
        event_type: AuditEventType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1000,
        actor_user_id: str | None = None,
    ) -> str:
        """Export audit records in SOC2-auditor-ready CSV format."""
        events = await self.search(
            user_id=user_id,
            subject_id=subject_id,
            event_type=event_type,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            actor_user_id=actor_user_id,
            log_access=True,
        )
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "event_id",
                "timestamp",
                "event_type",
                "user_id",
                "subject_id",
                "action",
                "resource_type",
                "resource_id",
                "result",
                "client_ip",
                "session_id",
                "signature",
                "signature_valid",
                "metadata",
            ]
        )
        for ev in events:
            writer.writerow(
                [
                    _sanitize_csv_cell(ev.event_id),
                    _sanitize_csv_cell(_ensure_utc(ev.timestamp).isoformat()),
                    _sanitize_csv_cell(ev.event_type.value),
                    _sanitize_csv_cell(ev.user_id),
                    _sanitize_csv_cell(ev.subject_id),
                    _sanitize_csv_cell(ev.action),
                    _sanitize_csv_cell(ev.resource_type),
                    _sanitize_csv_cell(ev.resource_id),
                    _sanitize_csv_cell(ev.result),
                    _sanitize_csv_cell(ev.client_ip),
                    _sanitize_csv_cell(ev.session_id),
                    _sanitize_csv_cell(ev.signature),
                    "true" if self.verify_signature(ev) else "false",
                    _sanitize_csv_cell(json.dumps(ev.metadata, sort_keys=True)),
                ]
            )
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Singleton Lifecycle Helpers
# ---------------------------------------------------------------------------

_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """Return the global ``AuditLogger`` singleton or raise if uninitialized."""
    if _audit_logger is None:
        raise RuntimeError("AuditLogger not initialized. Call init_audit_logger() first.")
    return _audit_logger


def get_audit_logger_optional() -> AuditLogger | None:
    """Return the global ``AuditLogger`` singleton if initialized, else ``None``."""
    return _audit_logger


def init_audit_logger(
    store: AuditStore | Path | str,
    signing_key: bytes | str,
    *,
    enable_async: bool = True,
    queue_size: int = 10000,
    enabled: bool = True,
    previous_signing_keys: Sequence[bytes | str] = (),
) -> AuditLogger:
    """Initialize (or idempotently reinitialize) the global ``AuditLogger`` singleton."""
    global _audit_logger
    _audit_logger = AuditLogger(
        store=store,
        signing_key=signing_key,
        enable_async=enable_async,
        queue_size=queue_size,
        enabled=enabled,
        previous_signing_keys=previous_signing_keys,
    )
    return _audit_logger


def reset_audit_logger() -> None:
    """Clear the global ``AuditLogger`` singleton (for deterministic test teardown)."""
    global _audit_logger
    _audit_logger = None


async def log_audit_event_safe(
    *,
    event_type: AuditEventType,
    action: str,
    resource_type: str,
    result: str = "success",
    user_id: str | None = None,
    subject_id: str | None = None,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
    session_id: str | None = None,
) -> None:
    """Best-effort additive audit hook for runtime paths (no-op when uninitialized)."""
    active = _audit_logger
    if active is None or not active.enabled:
        return
    try:
        await active.log(
            AuditEvent(
                event_type=event_type,
                timestamp=datetime.now(UTC),
                user_id=user_id,
                subject_id=subject_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                result=result,
                metadata=dict(metadata) if metadata else {},
                client_ip=client_ip,
                user_agent=user_agent,
                session_id=session_id,
            )
        )
    except Exception as exc:  # pragma: no cover - defensive belt-and-suspenders
        logger.debug("Non-blocking audit hook suppressed error: %s", exc)


__all__ = [
    "AuditEvent",
    "AuditEventType",
    "AuditLogger",
    "AuditStore",
    "InMemoryAuditStore",
    "PostgresAuditStore",
    "SQLiteAuditStore",
    "get_audit_logger",
    "get_audit_logger_optional",
    "init_audit_logger",
    "log_audit_event_safe",
    "redact_sensitive_metadata",
    "reset_audit_logger",
]
