"""
Tests for Durable Multi-Tenant Run & HITL Store (EVO-P3-05),
Unified SQL/Alembic Migration Chain, and Multi-Target Dockerfile (EVO-P3-03).
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from product_value_system.persistence.database import Database
from product_value_system.persistence.run_store import (
    ApprovalRecord,
    DurableRunRecord,
    SQLiteDurableRunStore,
    new_approval_id,
    new_run_id,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_sqlite_durable_run_store_survives_pod_restart(tmp_path: Path) -> None:
    """Runs and pending HITL approvals persist cleanly across full store restart."""
    db_file = tmp_path / "durable-restart.db"
    now = datetime.now(UTC)
    run_id = new_run_id()

    # Pod 1 creates a run and pauses at decision_gate
    store_pod1 = SQLiteDurableRunStore(db_file)
    await store_pod1.initialize()
    assert await store_pod1.health_check() is True

    await store_pod1.create_run(
        DurableRunRecord(
            run_id=run_id,
            tenant_id="tenant-durable",
            problem_id="PROB-900",
            problem_payload={"title": "Survive Restart"},
            status="waiting_approval",
            current_stage="decision_gate",
            created_by="eng-1",
            created_at=now,
            updated_at=now,
            stages={"discovery": {"status": "completed"}},
        )
    )
    await store_pod1.create_approval(
        ApprovalRecord(
            approval_id=new_approval_id(),
            run_id=run_id,
            tenant_id="tenant-durable",
            stage_name="decision_gate",
            status="pending",
            requested_at=now,
        )
    )
    await store_pod1.close()

    # Pod 2 boots fresh against the same DB and resolves the pending approval
    store_pod2 = SQLiteDurableRunStore(db_file)
    await store_pod2.initialize()
    recovered = await store_pod2.get_run(run_id, tenant_id="tenant-durable")
    assert recovered is not None
    assert recovered.status == "waiting_approval"
    assert len(recovered.approvals) == 1
    assert recovered.approvals[0].status == "pending"

    # Cross-tenant lookup must return None
    assert await store_pod2.get_run(run_id, tenant_id="other-tenant") is None

    resolved = await store_pod2.resolve_approval(
        run_id=run_id,
        tenant_id="tenant-durable",
        decision="approved",
        decided_by="pm-lead",
        comment="LGTM after restart",
    )
    assert resolved is not None
    assert resolved.status == "approved"
    assert resolved.decided_by == "pm-lead"
    await store_pod2.close()


def test_sqlite_migrations_apply_001_002_003_cleanly(tmp_path: Path) -> None:
    """``Database.migrate()`` applies all SQL migrations including 003 durable tables."""
    db_file = tmp_path / "migrated.db"
    db = Database(db_file, migrations_dir=REPO_ROOT / "migrations")
    db.migrate()

    tables = {
        row["name"]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "durable_runs" in tables
    assert "run_approvals" in tables
    assert "run_jobs" in tables
    assert "run_events" in tables

    versions = [
        int(row["version"])
        for row in db.execute(
            "SELECT version FROM schema_migrations ORDER BY version ASC"
        ).fetchall()
    ]
    assert versions == [1, 2, 3, 4]
    db.close()


def test_alembic_revision_graph_is_continuous() -> None:
    """Alembic migrations form an unbroken linear chain with no dangling down_revision."""
    alembic_ini = REPO_ROOT / "alembic.ini"
    alembic_env = REPO_ROOT / "alembic" / "env.py"
    versions_dir = REPO_ROOT / "alembic" / "versions"

    assert alembic_ini.is_file()
    assert alembic_env.is_file()

    revisions: dict[str, str | None] = {}
    for py_file in sorted(versions_dir.glob("*.py")):
        if py_file.name.startswith("__"):
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        rev: str | None = None
        down_rev: str | None = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "revision":
                        rev = ast.literal_eval(node.value)
                    elif isinstance(target, ast.Name) and target.id == "down_revision":
                        down_rev = ast.literal_eval(node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "revision" and node.value is not None:
                    rev = ast.literal_eval(node.value)
                elif node.target.id == "down_revision" and node.value is not None:
                    down_rev = ast.literal_eval(node.value)
        assert rev is not None, f"{py_file.name} missing revision"
        revisions[rev] = down_rev

    assert "001_initial_schema" in revisions
    assert "a1b2c3d4e5f6" in revisions
    assert "b2c3d4e5f607" in revisions

    roots = [rev for rev, down in revisions.items() if down is None]
    assert roots == ["001_initial_schema"]
    for rev, down in revisions.items():
        if down is not None:
            assert down in revisions, f"Dangling down_revision {down!r} in {rev!r}"


def test_multitarget_dockerfile_separates_runtime_and_ci_dev() -> None:
    """Dockerfile must enforce non-root runtime without dev deps + uvicorn ASGI entrypoint."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "AS builder-prod" in dockerfile
    assert "AS runtime" in dockerfile
    assert "AS ci-dev" in dockerfile

    prod_section = dockerfile.split("AS ci-dev")[0]
    assert ".[dev]" not in prod_section
    assert "USER pvs" in prod_section
    assert "product_value_system.api.app_factory:app" in prod_section
    assert "/health/live" in prod_section

    ci_section = dockerfile.split("AS ci-dev")[1]
    assert ".[dev]" in ci_section


@pytest.mark.asyncio
async def test_postgres_durable_run_store_full_lifecycle_and_atomic_approval() -> None:
    """Exercise all PostgresDurableRunStore methods including atomic double-resolve protection."""
    import json
    from typing import Any

    from product_value_system.persistence.run_store import PostgresDurableRunStore

    class _FakePostgresDB:
        def __init__(self) -> None:
            self.is_connected = False
            self.migrated = False
            self.runs: dict[tuple[str, str], dict[str, Any]] = {}
            self.approvals: dict[str, dict[str, Any]] = {}
            self.queries: list[str] = []

        async def connect(self) -> None:
            self.is_connected = True

        async def migrate(self) -> None:
            self.migrated = True

        async def close(self) -> None:
            self.is_connected = False

        def get_pool_stats(self) -> dict[str, Any]:
            return {"size": 4, "idle": 3}

        async def execute(self, query: str, *args: Any) -> str:
            self.queries.append(query)
            q = " ".join(query.split()).upper()
            if q.startswith("INSERT INTO DURABLE_RUNS"):
                (
                    run_id, tenant_id, problem_id, payload, status,
                    stage, created_by, created_at, updated_at,
                    completed_at, cost, stages, artifacts,
                ) = args
                self.runs[(run_id, tenant_id)] = {
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "problem_id": problem_id,
                    "problem_payload": json.loads(payload),
                    "status": status,
                    "current_stage": stage,
                    "created_by": created_by,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "completed_at": completed_at,
                    "total_cost_usd": cost,
                    "stages_json": json.loads(stages),
                    "artifacts_json": json.loads(artifacts),
                }
            elif q.startswith("UPDATE DURABLE_RUNS"):
                status, stage, updated_at, completed_at, cost, stages, artifacts, rid, tid = args
                rec = self.runs[(rid, tid)]
                rec.update(
                    {
                        "status": status,
                        "current_stage": stage,
                        "updated_at": updated_at,
                        "completed_at": completed_at,
                        "total_cost_usd": cost,
                        "stages_json": json.loads(stages),
                        "artifacts_json": json.loads(artifacts),
                    }
                )
            elif q.startswith("INSERT INTO RUN_APPROVALS"):
                aid, rid, tid, stage, status, req_at, dec_by, dec_at, comment = args
                self.approvals[aid] = {
                    "approval_id": aid,
                    "run_id": rid,
                    "tenant_id": tid,
                    "stage_name": stage,
                    "status": status,
                    "requested_at": req_at,
                    "decided_by": dec_by,
                    "decided_at": dec_at,
                    "comment": comment,
                }
            return "OK"

        async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
            self.queries.append(query)
            q = " ".join(query.split()).upper()
            if q == "SELECT 1 AS OK":
                return {"ok": 1}
            if q.startswith("SELECT * FROM DURABLE_RUNS"):
                return self.runs.get((args[0], args[1]))
            if q.startswith("UPDATE RUN_APPROVALS"):
                assert "FOR UPDATE SKIP LOCKED" in q
                assert "AND STATUS = 'PENDING'" in q
                assert "RETURNING" in q
                decision, dec_by, dec_at, comment, rid, tid = args
                candidates = sorted(
                    (
                        a
                        for a in self.approvals.values()
                        if a["run_id"] == rid
                        and a["tenant_id"] == tid
                        and a["status"] == "pending"
                    ),
                    key=lambda a: a["requested_at"],
                )
                if not candidates:
                    return None
                target = candidates[0]
                target["status"] = decision
                target["decided_by"] = dec_by
                target["decided_at"] = dec_at
                target["comment"] = comment
                return dict(target)
            return None

        async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
            self.queries.append(query)
            q = " ".join(query.split()).upper()
            if "FROM RUN_APPROVALS" in q:
                rid, tid = args
                return sorted(
                    (
                        a
                        for a in self.approvals.values()
                        if a["run_id"] == rid and a["tenant_id"] == tid
                    ),
                    key=lambda a: a["requested_at"],
                )
            if "FROM DURABLE_RUNS" in q:
                if "AND STATUS = $2" in q:
                    tid, status, limit, offset = args
                    rows = [
                        r
                        for r in self.runs.values()
                        if r["tenant_id"] == tid and r["status"] == status
                    ]
                else:
                    tid, limit, offset = args
                    rows = [r for r in self.runs.values() if r["tenant_id"] == tid]
                rows.sort(key=lambda r: r["created_at"], reverse=True)
                return rows[offset : offset + limit]
            return []

    fake_db = _FakePostgresDB()
    store = PostgresDurableRunStore(fake_db)
    assert await store.health_check() is False

    await store.initialize()
    assert fake_db.is_connected is True
    assert fake_db.migrated is True
    assert await store.health_check() is True
    assert store.get_pool_stats()["backend"] == "postgresql"

    now = datetime.now(UTC)
    run_id = new_run_id()
    run = DurableRunRecord(
        run_id=run_id,
        tenant_id="tenant-pg",
        problem_id="PROB-PG-1",
        problem_payload={"title": "Postgres Durable Run"},
        status="waiting_approval",
        current_stage="decision_gate",
        created_by="sre-1",
        created_at=now,
        updated_at=now,
    )
    await store.create_run(run)
    await store.create_approval(
        ApprovalRecord(
            approval_id=new_approval_id(),
            run_id=run_id,
            tenant_id="tenant-pg",
            stage_name="decision_gate",
            status="pending",
            requested_at=now,
        )
    )

    fetched = await store.get_run(run_id, tenant_id="tenant-pg")
    assert fetched is not None
    assert len(fetched.approvals) == 1
    assert await store.get_run(run_id, tenant_id="tenant-other") is None

    assert len(await store.list_runs(tenant_id="tenant-pg")) == 1
    assert len(await store.list_runs(tenant_id="tenant-pg", status="waiting_approval")) == 1
    assert len(await store.list_runs(tenant_id="tenant-pg", status="completed")) == 0

    with pytest.raises(ValueError, match="Invalid approval decision"):
        await store.resolve_approval(
            run_id=run_id,
            tenant_id="tenant-pg",
            decision="maybe",
            decided_by="pm-1",
        )

    # First resolution succeeds atomically; concurrent second resolution returns None
    first = await store.resolve_approval(
        run_id=run_id,
        tenant_id="tenant-pg",
        decision="approved",
        decided_by="pm-1",
        comment="Approved atomically",
    )
    assert first is not None and first.status == "approved"
    second = await store.resolve_approval(
        run_id=run_id,
        tenant_id="tenant-pg",
        decision="rejected",
        decided_by="pm-2",
        comment="Stale concurrent click",
    )
    assert second is None

    run.status = "completed"
    run.total_cost_usd = 0.42
    await store.update_run(run)
    updated = await store.get_run(run_id, tenant_id="tenant-pg")
    assert updated is not None and updated.status == "completed" and updated.total_cost_usd == 0.42

    await store.close()
    assert fake_db.is_connected is False


def test_rls_consistency_alembic_dialect_guard_and_zero_numpy_similarity_matcher() -> None:
    """Verify P0-02 dialect guard, P0-03 RLS consistency across 003/004, and P0-05 zero-NumPy."""
    sql_004 = (
        REPO_ROOT / "migrations" / "postgres" / "004_create_pgvector_semantic_store.sql"
    ).read_text(encoding="utf-8")
    assert "current_setting('app.current_tenant', true)" in sql_004
    assert "current_setting('app.current_tenant_id'" not in sql_004
    assert "IS NULL" in sql_004

    mig_001 = (REPO_ROOT / "alembic" / "versions" / "001_create_audit_tables.py").read_text(
        encoding="utf-8"
    )
    assert 'with_variant(JSONB(), "postgresql")' in mig_001
    assert 'op.get_bind().dialect.name == "postgresql"' in mig_001

    sim_src = (
        REPO_ROOT / "src" / "product_value_system" / "caching" / "similarity_matcher.py"
    ).read_text(encoding="utf-8")
    assert "import numpy" not in sim_src

    from product_value_system.caching.similarity_matcher import (
        cosine_similarity,
        local_prompt_embedding,
    )

    v1 = local_prompt_embedding("checkout conversion latency optimization")
    v2 = local_prompt_embedding("checkout conversion latency optimization")
    assert len(v1) == 256
    assert cosine_similarity(v1, v2) == pytest.approx(1.0, abs=1e-6)

    # Verify production ASGI composition root boots cleanly with neither numpy nor scipy installed
    import subprocess
    import sys

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.modules['numpy'] = None; sys.modules['scipy'] = None; "
                "import product_value_system.api.app_factory as m; "
                "assert hasattr(m, 'create_production_app')"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"ASGI boot failed without numpy/scipy:\n{proc.stderr}"
