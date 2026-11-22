"""
Async PostgreSQL persistence backend for multi-node Kubernetes deployments (v1.1).

Provides ``PostgresDatabase`` (``asyncpg`` connection pool + idempotent migrations),
``PostgresValueStreamRepository`` (full parity with ``ValueStreamRepository``), and
``create_database()`` factory auto-selecting PostgreSQL or SQLite WAL by DSN.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from product_value_system.persistence.database import Database
from product_value_system.persistence.models import (
    CostRecord,
    EvaluationRecord,
    PolicyDecisionRecord,
    RunRecord,
    StageRecord,
)

logger = logging.getLogger(__name__)


def _parse_json_field(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


class PostgresDatabase:
    """Async ``asyncpg`` connection pool manager with idempotent SQL migrations."""

    def __init__(
        self,
        dsn: str,
        *,
        migrations_dir: Path | str = "migrations/postgres",
        min_size: int = 2,
        max_size: int = 10,
        command_timeout: float = 30.0,
        pool: Any | None = None,
    ) -> None:
        if not dsn.startswith(("postgresql://", "postgres://")):
            raise ValueError(
                f"PostgreSQL DSN must start with postgresql:// or postgres://, got {dsn!r}"
            )
        self.dsn = dsn
        self.migrations_dir = Path(migrations_dir)
        self.min_size = max(1, int(min_size))
        self.max_size = max(self.min_size, int(max_size))
        self.command_timeout = float(command_timeout)
        self._pool: Any | None = pool

    @property
    def is_connected(self) -> bool:
        return self._pool is not None

    async def connect(self) -> None:
        """Create the ``asyncpg`` connection pool (idempotent if already connected)."""
        if self._pool is not None:
            return
        try:
            import asyncpg
        except ImportError as exc:
            raise ImportError(
                "asyncpg is required for PostgreSQL persistence. "
                "Install via `pip install 'product-value-system[postgres]'`."
            ) from exc

        self._pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=self.min_size,
            max_size=self.max_size,
            command_timeout=self.command_timeout,
        )
        logger.info(
            "Connected PostgresDatabase pool (min=%d, max=%d)", self.min_size, self.max_size
        )

    async def close(self) -> None:
        """Close the connection pool cleanly."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _require_pool(self) -> Any:
        if self._pool is None:
            raise RuntimeError("PostgresDatabase not connected; call `await db.connect()` first.")
        return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        """Acquire a pooled connection."""
        pool = self._require_pool()
        async with pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        """Acquire a pooled connection wrapped in an atomic database transaction."""
        async with self.acquire() as conn:
            async with conn.transaction():
                yield conn

    async def execute(self, sql: str, *args: Any) -> str:
        async with self.acquire() as conn:
            return str(await conn.execute(sql, *args))

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        async with self.acquire() as conn:
            rows = await conn.fetch(sql, *args)
            return [dict(r) for r in rows]

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        async with self.acquire() as conn:
            row = await conn.fetchrow(sql, *args)
            return dict(row) if row is not None else None

    async def health_check(self) -> bool:
        """Execute lightweight ``SELECT 1`` probe against PostgreSQL."""
        try:
            row = await self.fetchrow("SELECT 1 AS ok")
            return row is not None
        except Exception as exc:
            logger.warning("PostgreSQL health check failed: %s", exc)
            return False

    async def migrate(self) -> int:
        """Apply pending PostgreSQL migrations in version order and return count applied."""
        if self._pool is None:
            await self.connect()

        current_version = 0
        try:
            row = await self.fetchrow("SELECT MAX(version) AS version FROM schema_migrations")
            if row and row.get("version") is not None:
                current_version = int(row["version"])
        except Exception:
            current_version = 0

        applied = 0
        for migration_file in sorted(self.migrations_dir.glob("*.sql")):
            version = int(migration_file.stem.split("_")[0])
            if version > current_version:
                sql = migration_file.read_text(encoding="utf-8")
                async with self.transaction() as conn:
                    await conn.execute(sql)
                current_version = version
                applied += 1
        return applied


class PostgresValueStreamRepository:
    """Async multi-node repository mirroring ``ValueStreamRepository`` over PostgreSQL."""

    def __init__(self, database: PostgresDatabase) -> None:
        self.db = database

    async def save_run(self, run: RunRecord) -> None:
        """Insert a new value stream run."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO value_stream_runs
                (id, problem_id, status, started_at, completed_at,
                 total_cost_usd, outcome_achieved, artifact_dir)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                run.id,
                run.problem_id,
                run.status,
                run.started_at,
                run.completed_at,
                run.total_cost_usd,
                run.outcome_achieved,
                run.artifact_dir,
            )

    async def update_run(self, run: RunRecord) -> None:
        """Update run status, completion timestamp, total cost, and outcome flag."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                UPDATE value_stream_runs
                SET status = $1, completed_at = $2, total_cost_usd = $3, outcome_achieved = $4
                WHERE id = $5
                """,
                run.status,
                run.completed_at,
                run.total_cost_usd,
                run.outcome_achieved,
                run.id,
            )

    async def get_run(self, run_id: str) -> RunRecord | None:
        """Fetch a value stream run by ID."""
        row = await self.db.fetchrow("SELECT * FROM value_stream_runs WHERE id = $1", run_id)
        if row is None:
            return None
        started = _parse_dt(row["started_at"])
        assert started is not None
        return RunRecord(
            id=str(row["id"]),
            problem_id=str(row["problem_id"]),
            status=str(row["status"]),
            started_at=started,
            completed_at=_parse_dt(row.get("completed_at")),
            total_cost_usd=float(row.get("total_cost_usd", 0.0)),
            outcome_achieved=(
                bool(row["outcome_achieved"]) if row.get("outcome_achieved") is not None else None
            ),
            artifact_dir=row.get("artifact_dir"),
        )

    async def list_runs(self, limit: int = 100) -> list[RunRecord]:
        """List most recent value stream runs."""
        rows = await self.db.fetch(
            "SELECT * FROM value_stream_runs ORDER BY started_at DESC LIMIT $1",
            max(1, int(limit)),
        )
        results: list[RunRecord] = []
        for row in rows:
            started = _parse_dt(row["started_at"])
            assert started is not None
            results.append(
                RunRecord(
                    id=str(row["id"]),
                    problem_id=str(row["problem_id"]),
                    status=str(row["status"]),
                    started_at=started,
                    completed_at=_parse_dt(row.get("completed_at")),
                    total_cost_usd=float(row.get("total_cost_usd", 0.0)),
                    outcome_achieved=(
                        bool(row["outcome_achieved"])
                        if row.get("outcome_achieved") is not None
                        else None
                    ),
                    artifact_dir=row.get("artifact_dir"),
                )
            )
        return results

    async def save_stage(self, stage: StageRecord) -> None:
        """Persist stage execution record with JSONB metadata."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO stages
                (id, run_id, stage_name, agent_name, status, started_at,
                 completed_at, artifact_path, cost_usd, error, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                """,
                stage.id,
                stage.run_id,
                stage.stage_name,
                stage.agent_name,
                stage.status,
                stage.started_at,
                stage.completed_at,
                stage.artifact_path,
                stage.cost_usd,
                stage.error,
                json.dumps(stage.metadata or {}),
            )

    async def get_stages(self, run_id: str) -> list[StageRecord]:
        """Retrieve ordered stage records for a run."""
        rows = await self.db.fetch(
            "SELECT * FROM stages WHERE run_id = $1 ORDER BY started_at ASC",
            run_id,
        )
        stages: list[StageRecord] = []
        for row in rows:
            started = _parse_dt(row["started_at"])
            assert started is not None
            stages.append(
                StageRecord(
                    id=str(row["id"]),
                    run_id=str(row["run_id"]),
                    stage_name=str(row["stage_name"]),
                    agent_name=row.get("agent_name"),
                    status=str(row["status"]),
                    started_at=started,
                    completed_at=_parse_dt(row.get("completed_at")),
                    artifact_path=row.get("artifact_path"),
                    cost_usd=float(row.get("cost_usd", 0.0)),
                    error=row.get("error"),
                    metadata=_parse_json_field(row.get("metadata")),
                )
            )
        return stages

    async def save_policy_decision(self, decision: PolicyDecisionRecord) -> None:
        """Persist Cedar authorization audit record."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO policy_decisions
                (id, run_id, timestamp, agent_name, action, resource,
                 decision, policy_matched, rationale)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                decision.id,
                decision.run_id,
                decision.timestamp,
                decision.agent_name,
                decision.action,
                decision.resource,
                decision.decision,
                decision.policy_matched,
                decision.rationale,
            )

    async def save_evaluation(self, evaluation: EvaluationRecord) -> None:
        """Persist judge evaluation outcome."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO evaluations
                (id, run_id, stage_id, artifact_type, judge_name, judge_type,
                 score, passed, rationale, details)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
                """,
                evaluation.id,
                evaluation.run_id,
                evaluation.stage_id,
                evaluation.artifact_type,
                evaluation.judge_name,
                evaluation.judge_type,
                evaluation.score,
                evaluation.passed,
                evaluation.rationale,
                json.dumps(evaluation.details or {}),
            )

    async def save_cost(self, cost: CostRecord) -> None:
        """Persist token and USD cost telemetry record."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO costs
                (id, run_id, stage_id, timestamp, agent_name, model_name,
                 input_tokens, output_tokens, cost_usd)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                cost.id,
                cost.run_id,
                cost.stage_id,
                cost.timestamp,
                cost.agent_name,
                cost.model_name,
                cost.input_tokens,
                cost.output_tokens,
                cost.cost_usd,
            )


def create_database(
    dsn_or_path: Path | str,
    *,
    migrations_dir: Path | str | None = None,
    **kwargs: Any,
) -> Database | PostgresDatabase:
    """Instantiate ``PostgresDatabase`` for ``postgresql://`` DSNs or SQLite ``Database``."""
    raw = str(dsn_or_path).strip()
    if raw.startswith(("postgresql://", "postgres://")):
        return PostgresDatabase(
            raw,
            migrations_dir=migrations_dir or "migrations/postgres",
            **kwargs,
        )
    return Database(raw, migrations_dir=migrations_dir or "migrations")


__all__ = ["PostgresDatabase", "PostgresValueStreamRepository", "create_database"]
