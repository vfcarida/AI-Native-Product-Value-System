"""
Production-readiness hardening verification suite for v3.0.1:
1. Persistent append-only AuditStore (DeferredSQLiteAuditStore + DeferredPostgresAuditStore)
   wired into create_production_app() surviving full pod restart with HMAC verification
   and immutable SQLite BEFORE UPDATE / BEFORE DELETE triggers.
2. Multi-replica Kubernetes SSE streaming via RunEventBus (Redis Pub/Sub cross-pod
   fan-out AND durable DB tail polling fallback without Redis).
3. Turnkey LiveAgentStageExecutor wired into DurableRunWorker & create_production_app()
   via PVS_WORKER_EXECUTOR=live while preserving zero-NumPy/zero-SciPy ASGI startup.
4. Packaging, Dockerfile prompts/ parity, and Helm Chart 3.0.1 config/secret exposure.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.api.app_factory import ProductionSettings, create_production_app
from product_value_system.api.auth import DEFAULT_ROLE_PERMISSIONS, TenantContext
from product_value_system.api.routes.audit import AuditPrincipal
from product_value_system.api.worker import (
    LiveAgentStageExecutor,
    RunEventBus,
    SQLiteRunJobQueue,
)
from product_value_system.compliance.audit_logger import (
    AuditEvent,
    AuditEventType,
    DeferredPostgresAuditStore,
    DeferredSQLiteAuditStore,
)
from product_value_system.compliance.signing import generate_signing_key
from product_value_system.persistence.run_store import DurableRunRecord

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helpers: Fake async Redis Pub/Sub broker & Fake asyncpg pool
# ---------------------------------------------------------------------------


class _FakePubSubBroker:
    """In-memory multi-pod async Redis Pub/Sub broker for deterministic tests."""

    def __init__(self) -> None:
        self._channels: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    async def publish(self, channel: str, message: str) -> int:
        subscribers = list(self._channels.get(channel, ()))
        for q in subscribers:
            await q.put({"type": "message", "channel": channel, "data": message})
        return len(subscribers)

    def make_client(self) -> _FakeRedisClient:
        return _FakeRedisClient(self)


class _FakeRedisPubSub:
    def __init__(self, broker: _FakePubSubBroker) -> None:
        self._broker = broker
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribed: list[str] = []
        self._closed = False

    async def subscribe(self, *channels: str) -> None:
        for ch in channels:
            self._subscribed.append(ch)
            self._broker._channels.setdefault(ch, []).append(self._queue)

    async def unsubscribe(self, *channels: str) -> None:
        targets = channels or tuple(self._subscribed)
        for ch in targets:
            queues = self._broker._channels.get(ch, [])
            if self._queue in queues:
                queues.remove(self._queue)

    async def aclose(self) -> None:
        self._closed = True
        await self.unsubscribe()

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        while not self._closed:
            msg = await self._queue.get()
            yield msg


class _FakeRedisClient:
    def __init__(self, broker: _FakePubSubBroker) -> None:
        self._broker = broker

    async def publish(self, channel: str, message: str) -> int:
        return await self._broker.publish(channel, message)

    def pubsub(self) -> _FakeRedisPubSub:
        return _FakeRedisPubSub(self._broker)


class _FakeAsyncpgConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def execute(self, sql: str, *args: Any) -> str:
        if sql.strip().upper().startswith("INSERT INTO AUDIT_LOGS"):
            self._rows.append(
                {
                    "event_id": args[0],
                    "event_type": args[1],
                    "timestamp": args[2],
                    "user_id": args[3],
                    "subject_id": args[4],
                    "action": args[5],
                    "resource_type": args[6],
                    "resource_id": args[7],
                    "result": args[8],
                    "metadata": args[9],
                    "client_ip": args[10],
                    "user_agent": args[11],
                    "session_id": args[12],
                    "signature": args[13],
                }
            )
            return "INSERT 0 1"
        return "OK 0"

    async def fetch(self, _sql: str, *_args: Any) -> list[dict[str, Any]]:
        return list(self._rows)

    async def fetchval(self, _sql: str, *_args: Any) -> int:
        return len(self._rows)


class _FakeAsyncpgPool:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_FakeAsyncpgConn]:
        yield _FakeAsyncpgConn(self.rows)


class _FakePostgresDB:
    def __init__(self) -> None:
        self._pool: _FakeAsyncpgPool | None = None


# ---------------------------------------------------------------------------
# 1. Persistent Append-Only Audit Store in Control Plane
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_persistent_sqlite_audit_store_survives_app_restart_and_blocks_tampering(
    tmp_path: Path,
) -> None:
    """Control plane audit logs persist across restarts, verify HMACs, and block DELETE/UPDATE."""
    db_file = tmp_path / "control_plane_audit.db"
    signing_key = generate_signing_key()
    admin_perms = DEFAULT_ROLE_PERMISSIONS["admin"]

    def _build_settings() -> ProductionSettings:
        return ProductionSettings(
            database_url=f"sqlite:///{db_file}",
            audit_signing_key=signing_key,
            enable_tracing=False,
            enable_worker=False,
            api_keys={
                "pvs-admin-token": TenantContext(
                    tenant_id="tenant-acme",
                    user_id="alice",
                    role="admin",
                    permissions=admin_perms,
                )
            },
            audit_principals={
                "pvs-admin-token": AuditPrincipal(
                    user_id="alice",
                    role="admin",
                    permissions=admin_perms,
                )
            },
        )

    # Pod 1: create run (writes signed audit event into SQLite) then shut down cleanly
    app_pod1 = create_production_app(_build_settings())
    assert isinstance(app_pod1.state.audit_logger.store, DeferredSQLiteAuditStore)
    with TestClient(app_pod1) as client1:
        resp = client1.post(
            "/api/v1/runs",
            headers={"X-API-Key": "pvs-admin-token"},
            json={
                "problem_id": "PROB-AUDIT-01",
                "title": "Persist HMAC audit logs",
                "description": "Verify append-only SQLite audit persistence across restarts",
                "execute_async": False,
            },
        )
        assert resp.status_code == 201

    # Pod 2: cold restart against the same DB + signing key must read & verify persisted events
    app_pod2 = create_production_app(_build_settings())
    with TestClient(app_pod2) as client2:
        audit_resp = client2.get(
            "/api/v1/audit/search",
            headers={"X-API-Key": "pvs-admin-token"},
        )
        assert audit_resp.status_code == 200
        body = audit_resp.json()
        assert body["count"] >= 1
        assert body["integrity_verified"] is True
        assert body["tampered_event_ids"] == []
        assert any("run" in str(evt.get("resource_type", "")) for evt in body["events"])

        # Append-only SQLite trigger must reject DELETE and UPDATE attempts
        conn = app_pod2.state.audit_logger.store.connection
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM audit_logs")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE audit_logs SET action = 'tampered'")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deferred_postgres_audit_store_binds_pool_lazily() -> None:
    """DeferredPostgresAuditStore fails closed before pool init and works once connected."""
    fake_db = _FakePostgresDB()
    store = DeferredPostgresAuditStore(fake_db)

    event = AuditEvent(
        event_type=AuditEventType.CONFIG_CHANGED,
        timestamp=datetime.now(UTC),
        user_id="system",
        subject_id=None,
        action="boot_control_plane",
        resource_type="system",
        resource_id="pod-1",
        result="success",
    )

    with pytest.raises(RuntimeError, match="initialized PostgreSQL"):
        await store.insert(event)

    fake_db._pool = _FakeAsyncpgPool()
    await store.insert(event)
    assert await store.count() == 1
    rows = await store.query(limit=10)
    assert len(rows) == 1
    assert rows[0].action == "boot_control_plane"


# ---------------------------------------------------------------------------
# 2. Multi-Replica Kubernetes SSE Streaming (Redis Pub/Sub + DB Tail Fallback)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_event_bus_streams_across_replicas_via_redis_pubsub(tmp_path: Path) -> None:
    """Events published by Pod B reach Pod A SSE subscribers in real time via Redis Pub/Sub."""
    broker = _FakePubSubBroker()
    q_a = SQLiteRunJobQueue(tmp_path / "shared_redis.db")
    q_b = SQLiteRunJobQueue(tmp_path / "shared_redis.db")
    await q_a.initialize()
    await q_b.initialize()

    try:
        # High poll interval proves delivery came from Redis Pub/Sub rather than DB polling
        bus_pod_a = RunEventBus(
            q_a,
            redis_client=broker.make_client(),
            poll_interval_seconds=10.0,
        )
        bus_pod_b = RunEventBus(
            q_b,
            redis_client=broker.make_client(),
            poll_interval_seconds=10.0,
        )

        frames: list[str] = []

        async def _consume_pod_a() -> None:
            async for frame in bus_pod_a.stream_sse(
                "run-cross-redis",
                tenant_id="tenant-a",
                follow=True,
                heartbeat_seconds=5.0,
                idle_timeout_seconds=3.0,
            ):
                if frame.startswith(":"):
                    continue
                frames.append(frame)

        consumer = asyncio.create_task(_consume_pod_a())
        await asyncio.sleep(0.05)

        await bus_pod_b.publish(
            run_id="run-cross-redis",
            tenant_id="tenant-a",
            event_type="stage.started",
            stage_name="discovery",
            payload={"pod": "pod-b"},
        )
        await bus_pod_b.publish(
            run_id="run-cross-redis",
            tenant_id="tenant-a",
            event_type="run.completed",
            stage_name="completed",
            payload={"pod": "pod-b"},
        )

        await asyncio.wait_for(consumer, timeout=2.0)
        assert len(frames) == 2
        assert "event: stage.started" in frames[0]
        assert "event: run.completed" in frames[1]
    finally:
        await q_a.close()
        await q_b.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_event_bus_db_tail_fallback_streams_across_replicas_without_redis(
    tmp_path: Path,
) -> None:
    """Without Redis, Pod A still streams Pod B events via durable DB tail polling."""
    db_path = tmp_path / "shared_db_tail.db"
    q_a = SQLiteRunJobQueue(db_path)
    q_b = SQLiteRunJobQueue(db_path)
    await q_a.initialize()
    await q_b.initialize()

    try:
        bus_pod_a = RunEventBus(q_a, redis_client=None, poll_interval_seconds=0.04)
        bus_pod_b = RunEventBus(q_b, redis_client=None, poll_interval_seconds=0.04)

        frames: list[str] = []

        async def _consume_pod_a() -> None:
            async for frame in bus_pod_a.stream_sse(
                "run-cross-db",
                tenant_id="tenant-b",
                follow=True,
                heartbeat_seconds=5.0,
                idle_timeout_seconds=3.0,
            ):
                if frame.startswith(":"):
                    continue
                frames.append(frame)

        consumer = asyncio.create_task(_consume_pod_a())
        await asyncio.sleep(0.03)

        await bus_pod_b.publish(
            run_id="run-cross-db",
            tenant_id="tenant-b",
            event_type="stage.completed",
            stage_name="discovery",
            payload={"source": "pod-b"},
        )
        await bus_pod_b.publish(
            run_id="run-cross-db",
            tenant_id="tenant-b",
            event_type="run.completed",
            stage_name="completed",
            payload={"source": "pod-b"},
        )

        await asyncio.wait_for(consumer, timeout=2.0)
        assert len(frames) == 2
        assert "event: stage.completed" in frames[0]
        assert "event: run.completed" in frames[1]
    finally:
        await q_a.close()
        await q_b.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_event_bus_degrades_gracefully_when_redis_publish_fails(
    tmp_path: Path,
) -> None:
    """A failing Redis connection never blocks durable event persistence."""

    class _BrokenRedis:
        async def publish(self, _channel: str, _msg: str) -> int:
            raise ConnectionError("redis unreachable")

    queue = SQLiteRunJobQueue(tmp_path / "broken_redis.db")
    await queue.initialize()
    try:
        bus = RunEventBus(queue, redis_client=_BrokenRedis())
        evt = await bus.publish(
            run_id="run-degraded",
            tenant_id="tenant-c",
            event_type="run.started",
            stage_name="discovery",
        )
        assert evt.seq == 1
        persisted = await bus.list_events("run-degraded", tenant_id="tenant-c")
        assert len(persisted) == 1
    finally:
        await queue.close()


# ---------------------------------------------------------------------------
# 3. LiveAgentStageExecutor & PVS_WORKER_EXECUTOR=live Wiring
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_agent_stage_executor_runs_all_five_stages_through_harness() -> None:
    """LiveAgentStageExecutor executes every Value Stream stage via governed Harness."""
    adapter = MockModelAdapter(
        responses=[
            "Opportunity tree synthesized with conversion KPI.",
            "Executable specification with 3 acceptance criteria.",
            "4-perspective review completed with 0 critical issues.",
            "Minimal diff plan with unit test verification.",
            "SLO guardrails and rollback triggers configured.",
        ]
    )
    executor = LiveAgentStageExecutor(model_adapter=adapter, max_cost_usd=2.0)
    now = datetime.now(UTC)
    rec = DurableRunRecord(
        run_id="run-live-1",
        tenant_id="tenant-live",
        problem_id="PROB-LIVE-01",
        status="running",
        current_stage="discovery",
        created_by="alice",
        created_at=now,
        updated_at=now,
        problem_payload={"title": "Checkout latency reduction"},
    )

    for stage in ("discovery", "specification", "parallel_review", "build", "measurement"):
        out = await executor(rec, stage)
        assert out["artifact"]["executor"] == "live"
        assert out["summary"]
        rec.artifacts[stage] = out["artifact"]
        if stage == "build":
            assert out["diffs"]

    assert len(rec.artifacts) == 5


@pytest.mark.integration
def test_create_production_app_wires_live_worker_executor_from_settings(
    tmp_path: Path,
) -> None:
    """PVS_WORKER_EXECUTOR=live selects LiveAgentStageExecutor and completes async runs."""
    settings = ProductionSettings.from_env(
        {
            "DATABASE_URL": f"sqlite:///{tmp_path / 'live_worker.db'}",
            "PVS_WORKER_EXECUTOR": "live",
            "PVS_MODEL_PROVIDER": "mock",
            "PVS_ALLOW_ANONYMOUS_DEV": "true",
            "PVS_ENABLE_TRACING": "false",
        }
    )
    assert settings.worker_executor == "live"
    assert settings.model_provider == "mock"

    app = create_production_app(settings)
    assert app.state.worker is not None
    assert isinstance(app.state.worker._stage_executor, LiveAgentStageExecutor)

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/runs",
            json={
                "problem_id": "PROB-LIVE-CP",
                "title": "Live worker end-to-end",
                "description": "Execute all five stages through governed LiveAgentStageExecutor",
                "execute_async": True,
                "require_human_approval": False,
            },
        )
        assert resp.status_code == 201
        run_id = resp.json()["run_id"]

        # Drain worker deterministically by polling GET /api/v1/runs/{run_id}
        final_body: dict[str, Any] = {}
        for _ in range(50):
            r = client.get(f"/api/v1/runs/{run_id}")
            assert r.status_code == 200
            final_body = r.json()
            if final_body["status"] == "completed":
                break
            import time

            time.sleep(0.05)

        assert final_body["status"] == "completed"
        assert final_body["artifacts"]["discovery"]["executor"] == "live"
        assert final_body["artifacts"]["measurement"]["executor"] == "live"


# ---------------------------------------------------------------------------
# 4. Packaging, Dockerfile prompts/ Parity, Helm Exposure & Slim ASGI Boot
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_packaging_dockerfile_helm_and_slim_asgi_boot_parity() -> None:
    """Verify PyJWT[crypto] extra, Dockerfile prompts copy, Helm vars, and zero-NumPy ASGI boot."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    monitoring_deps = pyproject["project"]["optional-dependencies"]["monitoring"]
    assert any(dep.lower().startswith("pyjwt[crypto]") for dep in monitoring_deps)

    dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY --chown=pvs:pvs prompts/ ./prompts/" in dockerfile_text

    helm_dir = REPO_ROOT / "deploy" / "helm" / "product-value-system"
    configmap_text = (helm_dir / "templates" / "configmap.yaml").read_text(encoding="utf-8")
    secret_text = (helm_dir / "templates" / "secret.yaml").read_text(encoding="utf-8")
    schema = json.loads((helm_dir / "values.schema.json").read_text(encoding="utf-8"))
    assert "PVS_WORKER_EXECUTOR" in configmap_text
    assert "E2B_API_KEY" in secret_text
    assert schema["properties"]["config"]["properties"]["workerExecutor"]["enum"] == [
        "mock",
        "live",
    ]

    # Slim ASGI boot invariant: importing app_factory without numpy or scipy must succeed
    probe = (
        "import sys; "
        "sys.modules['numpy'] = None; "
        "sys.modules['scipy'] = None; "
        "import product_value_system.api.app_factory as af; "
        "assert af.app is not None"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Slim ASGI boot failed: {proc.stderr}"
