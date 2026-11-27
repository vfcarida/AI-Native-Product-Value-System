"""
Comprehensive tests for Stage 3 (v2.6):
- Durable async ``RunJobQueue`` (SQLite WAL + Postgres ``FOR UPDATE SKIP LOCKED``)
- Background ``DurableRunWorker`` (Discovery -> Spec -> Review -> HITL pause -> Auto-Resume)
- Real-time W3C Server-Sent Events (SSE) ``GET /api/v1/runs/{run_id}/events`` &
  ``Last-Event-ID`` replay + cross-tenant isolation
- Slack HITL approval resuming async worker automatically
- A2A SSE streaming ``GET /api/v1/a2a/tasks/{task_id}/events``
- Live ``alembic upgrade head`` across all 5 cross-dialect revisions on SQLite
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from starlette.testclient import TestClient

from product_value_system.api.app_factory import ProductionSettings, create_production_app
from product_value_system.api.auth import DEFAULT_ROLE_PERMISSIONS, TenantContext
from product_value_system.api.worker import (
    DurableRunWorker,
    PostgresRunJobQueue,
    RunEventBus,
    SQLiteRunJobQueue,
)
from product_value_system.integrations.slack_hitl import sign_slack_request
from product_value_system.persistence.run_store import (
    DurableRunRecord,
    SQLiteDurableRunStore,
    new_run_id,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_KEY = "test-v26-tenant-a-dev"
APPROVER_KEY = "test-v26-tenant-a-approver"
TENANT_B_KEY = "test-v26-tenant-b-admin"
SLACK_SECRET = "test-v26-slack-signing-secret"


def _parse_sse_frames(raw_text: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for chunk in raw_text.strip().split("\n\n"):
        lines = [ln for ln in chunk.splitlines() if ln and not ln.startswith(":")]
        if not lines:
            continue
        frame: dict[str, Any] = {}
        for ln in lines:
            if ln.startswith("id: "):
                frame["id"] = int(ln[4:].strip())
            elif ln.startswith("event: "):
                frame["event"] = ln[7:].strip()
            elif ln.startswith("data: "):
                frame["data"] = json.loads(ln[6:].strip())
        if frame:
            frames.append(frame)
    return frames


def _wait_for_status(
    client: TestClient,
    run_id: str,
    expected: str,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/runs/{run_id}", headers={"X-API-Key": DEV_KEY})
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] == expected:
            return last
        time.sleep(0.03)
    raise AssertionError(f"Run {run_id} did not reach status {expected!r}; last={last}")


def _build_async_app(db_path: Path) -> Any:
    settings = ProductionSettings(
        database_url=f"sqlite:///{db_path}",
        enable_devx=False,
        enable_audit=True,
        enable_tracing=False,
        enable_control_plane=True,
        enable_protocols=True,
        enable_integrations=True,
        enable_worker=True,
        slack_signing_secret=SLACK_SECRET,
        api_keys={
            DEV_KEY: TenantContext(
                tenant_id="tenant-a",
                user_id="dev-a",
                role="developer",
                permissions=DEFAULT_ROLE_PERMISSIONS["developer"],
            ),
            APPROVER_KEY: TenantContext(
                tenant_id="tenant-a",
                user_id="approver-a",
                role="approver",
                permissions=DEFAULT_ROLE_PERMISSIONS["approver"],
            ),
            TENANT_B_KEY: TenantContext(
                tenant_id="tenant-b",
                user_id="admin-b",
                role="admin",
                permissions=DEFAULT_ROLE_PERMISSIONS["admin"],
            ),
        },
    )
    return create_production_app(settings)


def test_async_worker_pauses_at_hitl_resumes_on_approve_and_streams_sse(tmp_path: Path) -> None:
    """Async run executes pre-gate stages, pauses at HITL, resumes via API, and streams SSE."""
    app = _build_async_app(tmp_path / "async-worker.db")

    with TestClient(app) as client:
        # 1. Enqueue async run requiring HITL approval
        create_resp = client.post(
            "/api/v1/runs",
            headers={"X-API-Key": DEV_KEY},
            json={
                "problem_id": "PROB-ASYNC-1",
                "title": "Async Checkout Resilience Upgrade",
                "require_human_approval": True,
                "execute_async": True,
            },
        )
        assert create_resp.status_code == 201
        created = create_resp.json()
        run_id = created["run_id"]
        assert created["status"] == "pending"
        assert created["job_id"].startswith("job-")

        # 2. Wait for background worker to execute discovery -> spec -> review -> pause at gate
        paused = _wait_for_status(client, run_id, "waiting_approval")
        assert paused["current_stage"] == "decision_gate"
        assert paused["stages"]["discovery"]["status"] == "completed"
        assert paused["stages"]["specification"]["status"] == "completed"
        assert paused["stages"]["parallel_review"]["status"] == "completed"
        assert len(paused["approvals"]) == 1
        assert paused["approvals"][0]["status"] == "pending"
        assert paused["total_cost_usd"] > 0.0

        # 3. SSE snapshot before approval + cross-tenant 404 enforcement
        assert (
            client.get(
                f"/api/v1/runs/{run_id}/events?follow=false",
                headers={"X-API-Key": TENANT_B_KEY},
            ).status_code
            == 404
        )
        sse_pre = client.get(
            f"/api/v1/runs/{run_id}/events?follow=false",
            headers={"X-API-Key": DEV_KEY},
        )
        assert sse_pre.status_code == 200
        assert sse_pre.headers["content-type"].startswith("text/event-stream")
        pre_frames = _parse_sse_frames(sse_pre.text)
        pre_types = [f["event"] for f in pre_frames]
        assert "run.queued" in pre_types
        assert "run.started" in pre_types
        assert "hitl.approval_requested" in pre_types
        cursor_seq = pre_frames[-1]["id"]

        # 4. Approve via Control Plane API -> enqueues resume job -> worker finishes build + learn
        approve_resp = client.post(
            f"/api/v1/runs/{run_id}/approve",
            headers={"X-API-Key": APPROVER_KEY},
            json={"comment": "Approved async resume"},
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] in {"running", "completed"}

        finished = _wait_for_status(client, run_id, "completed")
        assert finished["stages"]["build"]["status"] == "completed"
        assert finished["stages"]["measurement"]["status"] == "completed"
        assert "build" in finished["artifacts"]

        # 5. Last-Event-ID replay returns ONLY post-approval events (zero duplicates)
        sse_delta = client.get(
            f"/api/v1/runs/{run_id}/events?follow=false",
            headers={"X-API-Key": DEV_KEY, "Last-Event-ID": str(cursor_seq)},
        )
        assert sse_delta.status_code == 200
        delta_frames = _parse_sse_frames(sse_delta.text)
        assert all(f["id"] > cursor_seq for f in delta_frames)
        delta_types = [f["event"] for f in delta_frames]
        assert "hitl.approval_resolved" in delta_types
        assert "build.diff" in delta_types
        assert delta_types[-1] == "run.completed"

        # 6. A2A SSE streaming endpoint works for same tenant and blocks cross-tenant
        a2a_sse = client.get(
            f"/api/v1/a2a/tasks/{run_id}/events?follow=false",
            headers={"X-API-Key": DEV_KEY},
        )
        assert a2a_sse.status_code == 200
        assert "run.completed" in a2a_sse.text
        assert (
            client.get(
                f"/api/v1/a2a/tasks/{run_id}/events?follow=false",
                headers={"X-API-Key": TENANT_B_KEY},
            ).status_code
            == 404
        )


def test_slack_hitl_approval_resumes_async_worker_to_completion(tmp_path: Path) -> None:
    """Clicking Approve in Slack HITL automatically resumes a paused async worker run."""
    app = _build_async_app(tmp_path / "slack-async.db")

    with TestClient(app) as client:
        run_id = client.post(
            "/api/v1/runs",
            headers={"X-API-Key": DEV_KEY},
            json={
                "problem_id": "PROB-SLACK-ASYNC",
                "title": "Slack-Resumed Pipeline",
                "require_human_approval": True,
                "execute_async": True,
            },
        ).json()["run_id"]
        _wait_for_status(client, run_id, "waiting_approval")

        interaction = {
            "type": "block_actions",
            "user": {"id": "U777", "username": "lead.pm"},
            "actions": [
                {
                    "action_id": "pvs_approve_run",
                    "value": json.dumps({"run_id": run_id, "tenant_id": "tenant-a"}),
                }
            ],
        }
        form_body = urlencode({"payload": json.dumps(interaction)}).encode("utf-8")
        ts = str(int(time.time()))
        sig = sign_slack_request(form_body, ts, SLACK_SECRET)
        slack_resp = client.post(
            "/api/v1/webhooks/slack/interactions",
            content=form_body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": sig,
            },
        )
        assert slack_resp.status_code == 200
        assert slack_resp.json()["decision"] == "approved"

        completed = _wait_for_status(client, run_id, "completed")
        assert completed["stages"]["build"]["status"] == "completed"
        assert completed["stages"]["measurement"]["status"] == "completed"


@pytest.mark.asyncio
async def test_worker_retries_and_dead_letters_failing_jobs_with_sse_failure(
    tmp_path: Path,
) -> None:
    """Failing stage_executor retries until max_attempts, then dead-letters and emits run.failed."""
    db_file = tmp_path / "dlq.db"
    store = SQLiteDurableRunStore(db_file)
    queue = SQLiteRunJobQueue(db_file)
    bus = RunEventBus(queue)
    await store.initialize()
    await queue.initialize()

    attempts_seen = 0

    async def _always_fail(_rec: DurableRunRecord, _stage: str) -> dict[str, Any]:
        nonlocal attempts_seen
        attempts_seen += 1
        raise RuntimeError("transient LLM upstream timeout")

    now = datetime.now(UTC)
    run_id = new_run_id()
    await store.create_run(
        DurableRunRecord(
            run_id=run_id,
            tenant_id="tenant-dlq",
            problem_id="PROB-DLQ",
            problem_payload={"title": "DLQ test", "execution_mode": "async_worker"},
            status="pending",
            current_stage="queued",
            created_by="dev",
            created_at=now,
            updated_at=now,
        )
    )
    await queue.enqueue(run_id=run_id, tenant_id="tenant-dlq", job_type="start", max_attempts=2)

    worker = DurableRunWorker(
        run_store=store,
        job_queue=queue,
        event_bus=bus,
        stage_executor=_always_fail,
    )
    assert await worker.process_once() is True  # attempt 1 -> re-queued
    assert (await queue.queue_stats())["queued"] == 1
    assert await worker.process_once() is True  # attempt 2 -> dead_letter
    assert attempts_seen == 2
    assert (await queue.queue_stats())["dead_letter"] == 1

    rec = await store.get_run(run_id, tenant_id="tenant-dlq")
    assert rec is not None and rec.status == "failed"

    events = await bus.list_events(run_id, tenant_id="tenant-dlq")
    assert events[-1].event_type == "run.failed"
    assert events[-1].payload["dead_letter"] is True

    await queue.close()
    await store.close()


@pytest.mark.asyncio
async def test_postgres_run_job_queue_skip_locked_and_lease_reclaim() -> None:
    """PostgresRunJobQueue uses atomic FOR UPDATE SKIP LOCKED claims and supports lease reclaim."""

    class _FakePGQueueDB:
        def __init__(self) -> None:
            self.is_connected = False
            self.jobs: dict[str, dict[str, Any]] = {}
            self.events: list[dict[str, Any]] = []
            self.seq = 0

        async def connect(self) -> None:
            self.is_connected = True

        async def migrate(self) -> None:
            return None

        async def execute(self, query: str, *args: Any) -> str:
            q = " ".join(query.split()).upper()
            if q.startswith("INSERT INTO RUN_JOBS"):
                (
                    jid, rid, tid, jtype, status, payload, attempts, max_att,
                    wid, lease, err, created, updated, completed,
                ) = args
                self.jobs[jid] = {
                    "job_id": jid,
                    "run_id": rid,
                    "tenant_id": tid,
                    "job_type": jtype,
                    "status": status,
                    "payload_json": json.loads(payload),
                    "attempts": attempts,
                    "max_attempts": max_att,
                    "worker_id": wid,
                    "lease_expires_at": lease,
                    "last_error": err,
                    "created_at": created,
                    "updated_at": updated,
                    "completed_at": completed,
                }
            elif q.startswith("UPDATE RUN_JOBS SET STATUS = 'COMPLETED'"):
                now, jid = args
                self.jobs[jid]["status"] = "completed"
                self.jobs[jid]["completed_at"] = now
                self.jobs[jid]["lease_expires_at"] = None
            return "OK"

        async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
            q = " ".join(query.split()).upper()
            if "FOR UPDATE SKIP LOCKED" in q:
                worker_id, lease_until, now = args
                eligible = sorted(
                    (
                        j
                        for j in self.jobs.values()
                        if j["attempts"] < j["max_attempts"]
                        and (
                            j["status"] == "queued"
                            or (
                                j["status"] == "leased"
                                and j["lease_expires_at"] is not None
                                and j["lease_expires_at"] <= now
                            )
                        )
                    ),
                    key=lambda j: j["created_at"],
                )
                if not eligible:
                    return None
                target = eligible[0]
                target["status"] = "leased"
                target["worker_id"] = worker_id
                target["lease_expires_at"] = lease_until
                target["attempts"] += 1
                target["updated_at"] = now
                return dict(target)
            if q.startswith("UPDATE RUN_JOBS SET STATUS = CASE"):
                err, now, jid = args
                j = self.jobs[jid]
                j["status"] = "dead_letter" if j["attempts"] >= j["max_attempts"] else "queued"
                j["lease_expires_at"] = None
                j["last_error"] = err
                j["updated_at"] = now
                return dict(j)
            if q.startswith("INSERT INTO RUN_EVENTS"):
                rid, tid, etype, stage, payload, now = args
                self.seq += 1
                evt = {
                    "seq": self.seq,
                    "run_id": rid,
                    "tenant_id": tid,
                    "event_type": etype,
                    "stage_name": stage,
                    "payload_json": json.loads(payload),
                    "created_at": now,
                }
                self.events.append(evt)
                return dict(evt)
            return None

        async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
            q = " ".join(query.split()).upper()
            if "FROM RUN_EVENTS" in q:
                rid, tid, after_seq, limit = args
                return [
                    dict(e)
                    for e in self.events
                    if e["run_id"] == rid and e["tenant_id"] == tid and e["seq"] > after_seq
                ][:limit]
            if "GROUP BY STATUS" in q:
                counts: dict[str, int] = {}
                for j in self.jobs.values():
                    counts[j["status"]] = counts.get(j["status"], 0) + 1
                return [{"status": k, "cnt": v} for k, v in counts.items()]
            return []

    db = _FakePGQueueDB()
    pg_q = PostgresRunJobQueue(db)
    await pg_q.initialize()
    job = await pg_q.enqueue(run_id="run-pg-1", tenant_id="t1", job_type="start", max_attempts=2)
    claimed = await pg_q.claim_next("worker-1", lease_seconds=30.0)
    assert claimed is not None and claimed.job_id == job.job_id and claimed.attempts == 1
    assert await pg_q.claim_next("worker-2", lease_seconds=30.0) is None

    # Simulate worker crash (expired lease) -> worker-2 reclaims atomically
    db.jobs[job.job_id]["lease_expires_at"] = datetime.now(UTC) - timedelta(seconds=5)
    reclaimed = await pg_q.claim_next("worker-2", lease_seconds=30.0)
    assert reclaimed is not None and reclaimed.worker_id == "worker-2" and reclaimed.attempts == 2

    await pg_q.complete(job.job_id)
    assert (await pg_q.queue_stats())["completed"] == 1

    evt = await pg_q.append_event(
        run_id="run-pg-1", tenant_id="t1", event_type="run.completed", stage_name="completed"
    )
    fetched = await pg_q.fetch_events("run-pg-1", tenant_id="t1")
    assert len(fetched) == 1 and fetched[0].seq == evt.seq
    await pg_q.close()


@pytest.mark.asyncio
async def test_live_sse_stream_receives_concurrent_events_and_closes_on_terminal() -> None:
    """Live subscribers receive published events in real time and terminate on run.completed."""
    bus = RunEventBus()
    received: list[str] = []

    async def _consumer() -> None:
        async for frame in bus.stream_sse(
            "run-live-1", tenant_id="t-live", follow=True, idle_timeout_seconds=3.0
        ):
            received.append(frame)

    task = asyncio.create_task(_consumer())
    await asyncio.sleep(0.05)
    await bus.publish(run_id="run-live-1", tenant_id="t-live", event_type="stage.started")
    await bus.publish(run_id="run-live-1", tenant_id="t-live", event_type="run.completed")
    await asyncio.wait_for(task, timeout=3.0)

    frames = _parse_sse_frames("".join(received))
    assert [f["event"] for f in frames] == ["stage.started", "run.completed"]


def test_alembic_upgrade_head_executes_cleanly_on_sqlite(tmp_path: Path) -> None:
    """Verify full 5-revision Alembic chain & run live ``alembic upgrade head`` when installed."""
    import ast
    import os
    import shutil

    mig_004 = (
        REPO_ROOT / "alembic" / "versions" / "004_create_run_jobs_and_events.py"
    ).read_text(encoding="utf-8")
    ast.parse(mig_004)
    assert 'revision: str = "d4e5f6071829"' in mig_004
    assert 'down_revision: str | None = "c3d4e5f60718"' in mig_004
    assert 'with_variant(JSONB(), "postgresql")' in mig_004

    alembic_bin = shutil.which("alembic")
    if not alembic_bin:
        return

    db_file = tmp_path / "alembic-e2e.db"
    full_env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file.as_posix()}"}
    proc = subprocess.run(
        [alembic_bin, "upgrade", "head"],
        cwd=str(REPO_ROOT),
        env=full_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"alembic upgrade head failed:\n{proc.stderr}\n{proc.stdout}"
    assert db_file.is_file()

    import sqlite3

    conn = sqlite3.connect(db_file)
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {
            "alembic_version",
            "value_stream_runs",
            "audit_logs",
            "durable_runs",
            "run_approvals",
            "semantic_cache_entries",
            "semantic_memory_chunks",
            "run_jobs",
            "run_events",
        }.issubset(tables)
    finally:
        conn.close()
