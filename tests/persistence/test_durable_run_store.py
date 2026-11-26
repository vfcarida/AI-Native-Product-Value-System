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

    versions = [
        int(row["version"])
        for row in db.execute(
            "SELECT version FROM schema_migrations ORDER BY version ASC"
        ).fetchall()
    ]
    assert versions == [1, 2, 3]
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
