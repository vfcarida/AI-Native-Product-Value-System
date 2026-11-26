"""
Durable multi-tenant run state & asynchronous HITL approval store (EVO-P3-05).

Eliminates reliance on local pod filesystem JSON checkpoints (incompatible with
Kubernetes ``readOnlyRootFilesystem: true`` and horizontal scaling) by storing
run state, stage checkpoints, and pending human approvals in SQLite WAL or
async PostgreSQL.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

VALID_RUN_STATUSES = frozenset(
    {"pending", "running", "waiting_approval", "completed", "failed", "cancelled"}
)
VALID_APPROVAL_STATUSES = frozenset({"pending", "approved", "rejected"})


@dataclass
class ApprovalRecord:
    """Durable Human-in-the-Loop approval gate record."""

    approval_id: str
    run_id: str
    tenant_id: str
    stage_name: str
    status: str  # pending, approved, rejected
    requested_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "stage_name": self.stage_name,
            "status": self.status,
            "requested_at": self.requested_at.isoformat(),
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "comment": self.comment,
        }


@dataclass
class DurableRunRecord:
    """Tenant-scoped value stream run state persisted across pod restarts."""

    run_id: str
    tenant_id: str
    problem_id: str
    problem_payload: dict[str, Any]
    status: str
    current_stage: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    total_cost_usd: float = 0.0
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    approvals: list[ApprovalRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "problem_id": self.problem_id,
            "problem_payload": self.problem_payload,
            "status": self.status,
            "current_stage": self.current_stage,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "stages": self.stages,
            "artifacts": self.artifacts,
            "approvals": [a.to_dict() for a in self.approvals],
        }


@runtime_checkable
class DurableRunStore(Protocol):
    """Protocol for durable, tenant-scoped value stream run & HITL approval persistence."""

    async def initialize(self) -> None: ...

    async def health_check(self) -> bool: ...

    def get_pool_stats(self) -> dict[str, Any]: ...

    async def create_run(self, run: DurableRunRecord) -> DurableRunRecord: ...

    async def get_run(self, run_id: str, *, tenant_id: str) -> DurableRunRecord | None: ...

    async def list_runs(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DurableRunRecord]: ...

    async def update_run(self, run: DurableRunRecord) -> DurableRunRecord: ...

    async def create_approval(self, approval: ApprovalRecord) -> ApprovalRecord: ...

    async def resolve_approval(
        self,
        *,
        run_id: str,
        tenant_id: str,
        decision: str,
        decided_by: str,
        comment: str = "",
    ) -> ApprovalRecord | None: ...

    async def close(self) -> None: ...


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS durable_runs (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    problem_id TEXT NOT NULL,
    problem_payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled')
    ),
    current_stage TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    stages_json TEXT NOT NULL DEFAULT '{}',
    artifacts_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_durable_runs_tenant_status
    ON durable_runs(tenant_id, status, created_at);

CREATE TABLE IF NOT EXISTS run_approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    requested_at TEXT NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    comment TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (run_id) REFERENCES durable_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_run_approvals_run_tenant
    ON run_approvals(run_id, tenant_id, status);
"""


def _dt(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    return datetime.fromisoformat(str(val))


class SQLiteDurableRunStore:
    """Thread-safe SQLite WAL durable store for single-node & zero-dependency tests."""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    async def initialize(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            is_mem = self.db_path == ":memory:"
            if not is_mem:
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                self.db_path, timeout=30.0, check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            if not is_mem:
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.executescript(_SQLITE_SCHEMA)
            self._conn.commit()

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SQLiteDurableRunStore not initialized")
        return self._conn

    async def health_check(self) -> bool:
        with self._lock:
            if self._conn is None:
                return False
            try:
                row = self._conn.execute("SELECT 1").fetchone()
                return bool(row and row[0] == 1)
            except sqlite3.Error:
                return False

    def get_pool_stats(self) -> dict[str, Any]:
        return {
            "backend": "sqlite",
            "path": self.db_path,
            "connected": self._conn is not None,
        }

    def _load_approvals(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        tenant_id: str,
    ) -> list[ApprovalRecord]:
        rows = conn.execute(
            "SELECT * FROM run_approvals WHERE run_id = ? AND tenant_id = ? "
            "ORDER BY requested_at ASC",
            (run_id, tenant_id),
        ).fetchall()
        out: list[ApprovalRecord] = []
        for r in rows:
            req_at = _dt(r["requested_at"])
            assert req_at is not None
            out.append(
                ApprovalRecord(
                    approval_id=str(r["approval_id"]),
                    run_id=str(r["run_id"]),
                    tenant_id=str(r["tenant_id"]),
                    stage_name=str(r["stage_name"]),
                    status=str(r["status"]),
                    requested_at=req_at,
                    decided_by=str(r["decided_by"]) if r["decided_by"] is not None else None,
                    decided_at=_dt(r["decided_at"]),
                    comment=str(r["comment"] or ""),
                )
            )
        return out

    def _row_to_run(self, conn: sqlite3.Connection, row: sqlite3.Row) -> DurableRunRecord:
        created_at = _dt(row["created_at"])
        updated_at = _dt(row["updated_at"])
        assert created_at is not None and updated_at is not None
        return DurableRunRecord(
            run_id=str(row["run_id"]),
            tenant_id=str(row["tenant_id"]),
            problem_id=str(row["problem_id"]),
            problem_payload=json.loads(str(row["problem_payload"])),
            status=str(row["status"]),
            current_stage=str(row["current_stage"]),
            created_by=str(row["created_by"]),
            created_at=created_at,
            updated_at=updated_at,
            completed_at=_dt(row["completed_at"]),
            total_cost_usd=float(row["total_cost_usd"]),
            stages=json.loads(str(row["stages_json"])),
            artifacts=json.loads(str(row["artifacts_json"])),
            approvals=self._load_approvals(conn, str(row["run_id"]), str(row["tenant_id"])),
        )

    async def create_run(self, run: DurableRunRecord) -> DurableRunRecord:
        with self._lock:
            conn = self._require_conn()
            conn.execute(
                """
                INSERT INTO durable_runs (
                    run_id, tenant_id, problem_id, problem_payload, status,
                    current_stage, created_by, created_at, updated_at,
                    completed_at, total_cost_usd, stages_json, artifacts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.tenant_id,
                    run.problem_id,
                    json.dumps(run.problem_payload),
                    run.status,
                    run.current_stage,
                    run.created_by,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                    run.completed_at.isoformat() if run.completed_at else None,
                    float(run.total_cost_usd),
                    json.dumps(run.stages),
                    json.dumps(run.artifacts),
                ),
            )
            conn.commit()
            return run

    async def get_run(self, run_id: str, *, tenant_id: str) -> DurableRunRecord | None:
        with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                "SELECT * FROM durable_runs WHERE run_id = ? AND tenant_id = ?",
                (run_id, tenant_id),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_run(conn, row)

    async def list_runs(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DurableRunRecord]:
        with self._lock:
            conn = self._require_conn()
            clauses = ["tenant_id = ?"]
            params: list[Any] = [tenant_id]
            if status:
                clauses.append("status = ?")
                params.append(status)
            params.extend([max(1, min(limit, 200)), max(0, offset)])
            rows = conn.execute(
                f"SELECT * FROM durable_runs WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                tuple(params),
            ).fetchall()
            return [self._row_to_run(conn, r) for r in rows]

    async def update_run(self, run: DurableRunRecord) -> DurableRunRecord:
        with self._lock:
            conn = self._require_conn()
            run.updated_at = datetime.now(UTC)
            conn.execute(
                """
                UPDATE durable_runs
                SET status = ?, current_stage = ?, updated_at = ?, completed_at = ?,
                    total_cost_usd = ?, stages_json = ?, artifacts_json = ?
                WHERE run_id = ? AND tenant_id = ?
                """,
                (
                    run.status,
                    run.current_stage,
                    run.updated_at.isoformat(),
                    run.completed_at.isoformat() if run.completed_at else None,
                    float(run.total_cost_usd),
                    json.dumps(run.stages),
                    json.dumps(run.artifacts),
                    run.run_id,
                    run.tenant_id,
                ),
            )
            conn.commit()
            return run

    async def create_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        with self._lock:
            conn = self._require_conn()
            conn.execute(
                """
                INSERT INTO run_approvals (
                    approval_id, run_id, tenant_id, stage_name, status,
                    requested_at, decided_by, decided_at, comment
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.run_id,
                    approval.tenant_id,
                    approval.stage_name,
                    approval.status,
                    approval.requested_at.isoformat(),
                    approval.decided_by,
                    approval.decided_at.isoformat() if approval.decided_at else None,
                    approval.comment,
                ),
            )
            conn.commit()
            return approval

    async def resolve_approval(
        self,
        *,
        run_id: str,
        tenant_id: str,
        decision: str,
        decided_by: str,
        comment: str = "",
    ) -> ApprovalRecord | None:
        if decision not in {"approved", "rejected"}:
            raise ValueError(f"Invalid approval decision: {decision!r}")
        with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                """
                SELECT * FROM run_approvals
                WHERE run_id = ? AND tenant_id = ? AND status = 'pending'
                ORDER BY requested_at ASC LIMIT 1
                """,
                (run_id, tenant_id),
            ).fetchone()
            if row is None:
                return None
            now = datetime.now(UTC)
            conn.execute(
                """
                UPDATE run_approvals
                SET status = ?, decided_by = ?, decided_at = ?, comment = ?
                WHERE approval_id = ? AND tenant_id = ?
                """,
                (decision, decided_by, now.isoformat(), comment, row["approval_id"], tenant_id),
            )
            conn.commit()
            req_at = _dt(row["requested_at"])
            assert req_at is not None
            return ApprovalRecord(
                approval_id=str(row["approval_id"]),
                run_id=str(row["run_id"]),
                tenant_id=str(row["tenant_id"]),
                stage_name=str(row["stage_name"]),
                status=decision,
                requested_at=req_at,
                decided_by=decided_by,
                decided_at=now,
                comment=comment,
            )

    async def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


class PostgresDurableRunStore:
    """Async PostgreSQL durable run & approval store backed by ``PostgresDatabase`` pool."""

    def __init__(self, database: Any) -> None:
        self._db = database

    async def initialize(self) -> None:
        if not self._db.is_connected:
            await self._db.connect()
        await self._db.migrate()

    async def health_check(self) -> bool:
        if not self._db.is_connected:
            return False
        try:
            row = await self._db.fetchrow("SELECT 1 AS ok")
            return bool(row and row["ok"] == 1)
        except Exception:
            return False

    def get_pool_stats(self) -> dict[str, Any]:
        stats = self._db.get_pool_stats()
        stats["backend"] = "postgresql"
        return dict(stats)

    async def _load_approvals(self, run_id: str, tenant_id: str) -> list[ApprovalRecord]:
        rows = await self._db.fetch(
            "SELECT * FROM run_approvals WHERE run_id = $1 AND tenant_id = $2 "
            "ORDER BY requested_at ASC",
            run_id,
            tenant_id,
        )
        out: list[ApprovalRecord] = []
        for r in rows:
            req_at = _dt(r["requested_at"])
            assert req_at is not None
            out.append(
                ApprovalRecord(
                    approval_id=str(r["approval_id"]),
                    run_id=str(r["run_id"]),
                    tenant_id=str(r["tenant_id"]),
                    stage_name=str(r["stage_name"]),
                    status=str(r["status"]),
                    requested_at=req_at,
                    decided_by=str(r["decided_by"]) if r["decided_by"] is not None else None,
                    decided_at=_dt(r["decided_at"]),
                    comment=str(r["comment"] or ""),
                )
            )
        return out

    async def _row_to_run(self, row: Any) -> DurableRunRecord:
        created_at = _dt(row["created_at"])
        updated_at = _dt(row["updated_at"])
        assert created_at is not None and updated_at is not None

        def _as_dict(raw: Any) -> dict[str, Any]:
            if isinstance(raw, dict):
                return raw
            return dict(json.loads(str(raw))) if raw else {}

        return DurableRunRecord(
            run_id=str(row["run_id"]),
            tenant_id=str(row["tenant_id"]),
            problem_id=str(row["problem_id"]),
            problem_payload=_as_dict(row["problem_payload"]),
            status=str(row["status"]),
            current_stage=str(row["current_stage"]),
            created_by=str(row["created_by"]),
            created_at=created_at,
            updated_at=updated_at,
            completed_at=_dt(row["completed_at"]),
            total_cost_usd=float(row["total_cost_usd"]),
            stages=_as_dict(row["stages_json"]),
            artifacts=_as_dict(row["artifacts_json"]),
            approvals=await self._load_approvals(str(row["run_id"]), str(row["tenant_id"])),
        )

    async def create_run(self, run: DurableRunRecord) -> DurableRunRecord:
        await self._db.execute(
            """
            INSERT INTO durable_runs (
                run_id, tenant_id, problem_id, problem_payload, status,
                current_stage, created_by, created_at, updated_at,
                completed_at, total_cost_usd, stages_json, artifacts_json
            ) VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb)
            """,
            run.run_id,
            run.tenant_id,
            run.problem_id,
            json.dumps(run.problem_payload),
            run.status,
            run.current_stage,
            run.created_by,
            run.created_at,
            run.updated_at,
            run.completed_at,
            float(run.total_cost_usd),
            json.dumps(run.stages),
            json.dumps(run.artifacts),
        )
        return run

    async def get_run(self, run_id: str, *, tenant_id: str) -> DurableRunRecord | None:
        row = await self._db.fetchrow(
            "SELECT * FROM durable_runs WHERE run_id = $1 AND tenant_id = $2",
            run_id,
            tenant_id,
        )
        return None if row is None else await self._row_to_run(row)

    async def list_runs(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DurableRunRecord]:
        capped = max(1, min(limit, 200))
        off = max(0, offset)
        if status:
            rows = await self._db.fetch(
                "SELECT * FROM durable_runs WHERE tenant_id = $1 AND status = $2 "
                "ORDER BY created_at DESC LIMIT $3 OFFSET $4",
                tenant_id,
                status,
                capped,
                off,
            )
        else:
            rows = await self._db.fetch(
                "SELECT * FROM durable_runs WHERE tenant_id = $1 "
                "ORDER BY created_at DESC LIMIT $2 OFFSET $3",
                tenant_id,
                capped,
                off,
            )
        return [await self._row_to_run(r) for r in rows]

    async def update_run(self, run: DurableRunRecord) -> DurableRunRecord:
        run.updated_at = datetime.now(UTC)
        await self._db.execute(
            """
            UPDATE durable_runs
            SET status = $1, current_stage = $2, updated_at = $3, completed_at = $4,
                total_cost_usd = $5, stages_json = $6::jsonb, artifacts_json = $7::jsonb
            WHERE run_id = $8 AND tenant_id = $9
            """,
            run.status,
            run.current_stage,
            run.updated_at,
            run.completed_at,
            float(run.total_cost_usd),
            json.dumps(run.stages),
            json.dumps(run.artifacts),
            run.run_id,
            run.tenant_id,
        )
        return run

    async def create_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        await self._db.execute(
            """
            INSERT INTO run_approvals (
                approval_id, run_id, tenant_id, stage_name, status,
                requested_at, decided_by, decided_at, comment
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            approval.approval_id,
            approval.run_id,
            approval.tenant_id,
            approval.stage_name,
            approval.status,
            approval.requested_at,
            approval.decided_by,
            approval.decided_at,
            approval.comment,
        )
        return approval

    async def resolve_approval(
        self,
        *,
        run_id: str,
        tenant_id: str,
        decision: str,
        decided_by: str,
        comment: str = "",
    ) -> ApprovalRecord | None:
        if decision not in {"approved", "rejected"}:
            raise ValueError(f"Invalid approval decision: {decision!r}")
        row = await self._db.fetchrow(
            """
            SELECT * FROM run_approvals
            WHERE run_id = $1 AND tenant_id = $2 AND status = 'pending'
            ORDER BY requested_at ASC LIMIT 1
            """,
            run_id,
            tenant_id,
        )
        if row is None:
            return None
        now = datetime.now(UTC)
        await self._db.execute(
            """
            UPDATE run_approvals
            SET status = $1, decided_by = $2, decided_at = $3, comment = $4
            WHERE approval_id = $5 AND tenant_id = $6
            """,
            decision,
            decided_by,
            now,
            comment,
            row["approval_id"],
            tenant_id,
        )
        req_at = _dt(row["requested_at"])
        assert req_at is not None
        return ApprovalRecord(
            approval_id=str(row["approval_id"]),
            run_id=str(row["run_id"]),
            tenant_id=str(row["tenant_id"]),
            stage_name=str(row["stage_name"]),
            status=decision,
            requested_at=req_at,
            decided_by=decided_by,
            decided_at=now,
            comment=comment,
        )

    async def close(self) -> None:
        if self._db.is_connected:
            await self._db.close()


def new_run_id() -> str:
    return f"run-{uuid4().hex[:12]}"


def new_approval_id() -> str:
    return f"apr-{uuid4().hex[:12]}"
