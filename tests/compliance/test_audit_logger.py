"""
Comprehensive tests for PROD-006 Compliance Audit Trail, Signing, Immutability,
RBAC Search API, Quarterly Access Reviews, Config Contracts, and Migrations (25+ tests).
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from product_value_system.agents.agent import AgentResult
from product_value_system.agents.base_agent import BaseAgent
from product_value_system.api.health import create_app
from product_value_system.api.routes.audit import AuditPrincipal
from product_value_system.compliance import (
    AccessRecord,
    AuditEvent,
    AuditEventType,
    AuditLogger,
    AuditSigner,
    GDPRHandler,
    InMemoryAuditStore,
    InMemoryGDPRDataStore,
    QuarterlyAccessReviewer,
    SQLiteAuditStore,
    canonicalize_payload,
    generate_signing_key,
    get_audit_logger,
    init_audit_logger,
    load_signing_key_from_env,
    redact_sensitive_metadata,
    reset_audit_logger,
)
from product_value_system.contracts.config_schemas import (
    ComplianceConfigFile,
    validate_config_file,
)
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.persistence.database import Database

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_SIGNING_KEY = b"test_signing_key_32_bytes_prod06"


def _make_event(
    *,
    event_type: AuditEventType = AuditEventType.USER_LOGIN,
    user_id: str | None = "user123",
    subject_id: str | None = None,
    action: str = "User logged in successfully",
    resource_type: str = "authentication",
    resource_id: str | None = None,
    result: str = "success",
    metadata: dict[str, Any] | None = None,
    client_ip: str | None = "192.168.1.1",
    user_agent: str | None = "pytest-agent/1.0",
    session_id: str | None = "sess_abc123",
    timestamp: datetime | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_type=event_type,
        timestamp=timestamp or datetime.now(UTC),
        user_id=user_id,
        subject_id=subject_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        metadata=metadata if metadata is not None else {},
        client_ip=client_ip,
        user_agent=user_agent,
        session_id=session_id,
    )


@pytest.fixture
def sqlite_store(tmp_path: Path) -> SQLiteAuditStore:
    store = SQLiteAuditStore(tmp_path / "audit.db")
    yield store
    store.close()


@pytest.fixture
def audit_logger(sqlite_store: SQLiteAuditStore) -> AuditLogger:
    return AuditLogger(sqlite_store, TEST_SIGNING_KEY, enable_async=False)


# ---------------------------------------------------------------------------
# 1. Cryptographic Signing & Tamper Detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_signing_key_generation_and_env_loading(tmp_path: Path) -> None:
    """Signing keys generate >=32 secure bytes and load from env or secret file."""
    key = generate_signing_key(32)
    assert len(key) == 32

    with pytest.raises(ValueError):
        generate_signing_key(8)

    loaded_hex = load_signing_key_from_env(env={"AUDIT_SIGNING_KEY": key.hex()})
    assert loaded_hex == key

    key_file = tmp_path / "audit.key"
    key_file.write_text(key.hex(), encoding="utf-8")
    loaded_file = load_signing_key_from_env(env={"AUDIT_SIGNING_KEY_FILE": str(key_file)})
    assert loaded_file == key

    with pytest.raises(RuntimeError):
        load_signing_key_from_env(env={})


@pytest.mark.unit
def test_canonical_payload_is_order_independent() -> None:
    """Canonical serialization produces identical bytes regardless of dict key ordering."""
    p1 = {"b": 2, "a": {"y": 1, "x": 0}, "ts": datetime(2026, 11, 23, 12, 0, tzinfo=UTC)}
    p2 = {"ts": datetime(2026, 11, 23, 12, 0, tzinfo=UTC), "a": {"x": 0, "y": 1}, "b": 2}
    assert canonicalize_payload(p1) == canonicalize_payload(p2)


@pytest.mark.asyncio
async def test_signature_verification_and_tamper_detection(audit_logger: AuditLogger) -> None:
    """HMAC-SHA256 signature verifies untouched events and rejects any field tampering."""
    event = _make_event(
        event_type=AuditEventType.DATA_READ,
        user_id="user456",
        subject_id="data_123",
        action="User read sensitive data",
        resource_type="data",
        resource_id="data_123",
    )
    await audit_logger.log(event)
    assert event.event_id is not None
    assert event.signature is not None
    assert len(event.signature) == 64
    assert audit_logger.verify_signature(event) is True

    # Tamper with action -> must fail 100%
    event.action = "TAMPERED"
    assert audit_logger.verify_signature(event) is False


@pytest.mark.unit
def test_signer_supports_zero_downtime_key_rotation_and_chain_hashing() -> None:
    """Rotated AuditSigner still verifies historical events signed with previous keys."""
    old_key = generate_signing_key(32)
    new_key = generate_signing_key(32)
    payload = {"event_type": "user.login", "user_id": "alice", "action": "login"}

    old_signer = AuditSigner(old_key)
    old_sig = old_signer.sign(payload)

    rotated_signer = AuditSigner(new_key, previous_keys=[old_key])
    assert rotated_signer.verify(payload, old_sig) is True
    assert rotated_signer.verify(payload, "0" * 64) is False
    assert rotated_signer.verify(payload, "short") is False

    chain1 = AuditSigner.compute_chain_hash("genesis", old_sig)
    chain2 = AuditSigner.compute_chain_hash(chain1, rotated_signer.sign(payload))
    assert len(chain1) == 64 and len(chain2) == 64 and chain1 != chain2


# ---------------------------------------------------------------------------
# 2. Persistence & Append-Only Immutability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_persists_to_sqlite(
    audit_logger: AuditLogger,
    sqlite_store: SQLiteAuditStore,
) -> None:
    """Audit event persists all 14 compliance fields to SQLite WAL."""
    event = _make_event(metadata={"ip": "192.168.1.1", "method": "sso"})
    await audit_logger.log(event)

    row = sqlite_store.connection.execute(
        "SELECT * FROM audit_logs WHERE user_id = ?", ("user123",)
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "user.login"
    assert row["user_id"] == "user123"
    assert row["action"] == "User logged in successfully"
    assert len(row["signature"]) == 64
    assert await sqlite_store.count() == 1


@pytest.mark.asyncio
async def test_audit_log_immutable_blocks_delete_and_update(
    audit_logger: AuditLogger,
    sqlite_store: SQLiteAuditStore,
) -> None:
    """Database triggers unconditionally prohibit DELETE and UPDATE on audit_logs."""
    event = _make_event(
        event_type=AuditEventType.DATA_WRITE,
        user_id="attacker",
        action="Malicious action",
        resource_type="data",
        resource_id="sensitive_data",
    )
    await audit_logger.log(event)

    with pytest.raises(sqlite3.IntegrityError, match="DELETE is prohibited"):
        sqlite_store.connection.execute("DELETE FROM audit_logs WHERE user_id = ?", ("attacker",))

    with pytest.raises(sqlite3.IntegrityError, match="UPDATE is prohibited"):
        sqlite_store.connection.execute(
            "UPDATE audit_logs SET action = 'COVERED_TRACKS' WHERE user_id = ?",
            ("attacker",),
        )

    assert await sqlite_store.count() == 1


@pytest.mark.unit
def test_in_memory_store_enforces_append_only() -> None:
    """InMemoryAuditStore rejects direct update/delete calls."""
    store = InMemoryAuditStore()
    with pytest.raises(PermissionError):
        store.update()
    with pytest.raises(PermissionError):
        store.delete()


@pytest.mark.unit
def test_all_22_audit_event_types_exist() -> None:
    """All 22 required compliance event types are defined."""
    assert len(AuditEventType) == 22
    assert AuditEventType.AUDIT_LOG_ACCESSED.value == "audit.log.accessed"
    assert AuditEventType.GDPR_EXPORT_REQUESTED.value == "gdpr.export.requested"
    assert AuditEventType.GDPR_DELETION_REQUESTED.value == "gdpr.deletion.requested"


# ---------------------------------------------------------------------------
# 3. Search, Filtering, Meta-Audit & CSV Export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_audit_logs_filters_and_pagination(audit_logger: AuditLogger) -> None:
    """Audit logs can be queried by user_id, subject_id, event_type, time range, and offset."""
    base_time = datetime(2026, 11, 23, 10, 0, 0, tzinfo=UTC)
    for i in range(5):
        await audit_logger.log(
            _make_event(
                event_type=AuditEventType.MODEL_CALLED,
                user_id=f"user{i}",
                subject_id=f"subj{i % 2}",
                action=f"Model call {i}",
                resource_type="model",
                resource_id="gpt-4o",
                metadata={"tokens": 100 + i},
                session_id=f"sess_{i}",
                timestamp=base_time + timedelta(minutes=i),
            )
        )

    by_user = await audit_logger.search(user_id="user2")
    assert len(by_user) == 1
    assert by_user[0].user_id == "user2"
    assert audit_logger.verify_signature(by_user[0])

    by_type = await audit_logger.search(event_type=AuditEventType.MODEL_CALLED)
    assert len(by_type) == 5

    by_subject = await audit_logger.search(subject_id="subj0")
    assert len(by_subject) == 3

    window = await audit_logger.search(
        start_time=base_time + timedelta(minutes=1),
        end_time=base_time + timedelta(minutes=3),
    )
    assert len(window) == 3

    paged = await audit_logger.search(limit=2, offset=2)
    assert len(paged) == 2


@pytest.mark.asyncio
async def test_meta_audit_logged_on_search_access(audit_logger: AuditLogger) -> None:
    """Searching with log_access=True records an AUDIT_LOG_ACCESSED meta-audit event."""
    await audit_logger.log(_make_event(user_id="target_user"))
    _ = await audit_logger.search(
        user_id="target_user",
        actor_user_id="sec_officer_1",
        log_access=True,
    )
    meta_events = await audit_logger.search(event_type=AuditEventType.AUDIT_LOG_ACCESSED)
    assert len(meta_events) == 1
    assert meta_events[0].user_id == "sec_officer_1"
    assert meta_events[0].metadata["result_count"] == 1


@pytest.mark.asyncio
async def test_export_csv_includes_validity_and_neutralizes_formula_injection(
    audit_logger: AuditLogger,
) -> None:
    """SOC2 CSV export includes signature_valid=true and neutralizes Excel formula cells."""
    await audit_logger.log(
        _make_event(
            user_id="alice",
            action="=CMD|' /C calc'!A0",
            resource_type="report",
        )
    )
    csv_text = await audit_logger.export_csv(user_id="alice", actor_user_id="auditor_1")
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert len(rows) == 1
    assert rows[0]["signature_valid"] == "true"
    assert rows[0]["action"].startswith("'=")


# ---------------------------------------------------------------------------
# 4. Async Queue Worker, Latency Benchmark (<2ms) & Resilience Fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_mode_queues_and_flushes_events(sqlite_store: SQLiteAuditStore) -> None:
    """Async AuditLogger persists queued events cleanly and shuts down without loss."""
    al = AuditLogger(sqlite_store, TEST_SIGNING_KEY, enable_async=True, queue_size=100)
    for i in range(25):
        await al.log(_make_event(user_id=f"async_user_{i}", action=f"Event {i}"))
    await al.flush(timeout=5.0)

    stored = await al.search(limit=50)
    assert len(stored) == 25
    assert all(al.verify_signature(ev) for ev in stored)
    await al.close()


@pytest.mark.unit
def test_performance_overhead_under_2ms(benchmark: Any) -> None:
    """Critical-path async audit enqueue + HMAC-SHA256 signing adds <2ms latency."""
    store = InMemoryAuditStore()

    async def _run_batch() -> None:
        al = AuditLogger(store, TEST_SIGNING_KEY, enable_async=True, queue_size=10000)
        event = _make_event(
            event_type=AuditEventType.DATA_READ,
            user_id="perf_test",
            action="Performance test",
            resource_type="test",
        )
        await al.log(event)
        await al.close()

    # Measure direct non-blocking log() call inside an active loop
    loop = asyncio.new_event_loop()
    try:
        al = AuditLogger(store, TEST_SIGNING_KEY, enable_async=True, queue_size=50000)
        event = _make_event(
            event_type=AuditEventType.DATA_READ,
            user_id="perf_test",
            action="Performance test",
            resource_type="test",
        )
        # Warm worker start
        loop.run_until_complete(al.log(event))

        def _invoke() -> None:
            loop.run_until_complete(
                al.log(
                    _make_event(
                        event_type=AuditEventType.DATA_READ,
                        user_id="perf_test",
                        action="Performance test",
                        resource_type="test",
                    )
                )
            )

        benchmark(_invoke)
        loop.run_until_complete(al.close())
    finally:
        loop.close()

    # PROD-006 blocking invariant: mean latency strictly < 2ms (0.002s)
    assert benchmark.stats["mean"] < 0.002


@pytest.mark.asyncio
async def test_emergency_log_fallback_on_db_failure(
    audit_logger: AuditLogger,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Persistence failures never crash the app and write AUDIT_EMERGENCY JSON to stderr."""

    async def failing_persist(_event: AuditEvent) -> None:
        raise RuntimeError("DB unavailable")

    monkeypatch.setattr(audit_logger, "_persist_event", failing_persist)

    event = _make_event(
        event_type=AuditEventType.SECRET_ACCESSED,
        user_id="user999",
        action="Secret accessed",
        resource_type="secret",
        resource_id="api_key",
    )
    await audit_logger.log(event)

    captured = capsys.readouterr()
    assert "AUDIT_EMERGENCY" in captured.err
    assert "user999" in captured.err
    payload = json.loads(captured.err.strip().splitlines()[-1])
    assert payload["level"] == "AUDIT_EMERGENCY"
    assert payload["event"]["user_id"] == "user999"
    assert audit_logger.emergency_fallbacks == 1


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_repeated_failures(
    sqlite_store: SQLiteAuditStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """After consecutive DB failures, circuit breaker short-circuits to emergency log."""
    al = AuditLogger(
        sqlite_store,
        TEST_SIGNING_KEY,
        enable_async=False,
        circuit_breaker_threshold=2,
        circuit_breaker_cooldown_seconds=60.0,
    )
    calls = 0

    async def failing_persist(_event: AuditEvent) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("DB down")

    monkeypatch.setattr(al, "_persist_event", failing_persist)

    for i in range(4):
        await al.log(_make_event(user_id=f"cb_{i}"))

    # Only first 2 attempts hit failing_persist; next 2 short-circuited via open breaker
    assert calls == 2
    assert al.emergency_fallbacks == 4
    assert "circuit_breaker_open" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_sensitive_metadata_fields_are_redacted(audit_logger: AuditLogger) -> None:
    """Passwords, API keys, and tokens in metadata are redacted before signing & storing."""
    raw = {
        "api_key": "synthetic-placeholder-value",
        "nested": {"password": "my-password", "safe_flag": True},
        "model": "gpt-4o",
    }
    redacted = redact_sensitive_metadata(raw)
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert redacted["nested"]["safe_flag"] is True

    event = _make_event(metadata=raw)
    await audit_logger.log(event)
    stored = (await audit_logger.search(user_id="user123"))[0]
    assert stored.metadata["api_key"] == "[REDACTED]"
    assert stored.metadata["nested"]["password"] == "[REDACTED]"
    assert stored.metadata["model"] == "gpt-4o"
    assert audit_logger.verify_signature(stored)


@pytest.mark.asyncio
async def test_disabled_audit_logger_is_noop(sqlite_store: SQLiteAuditStore) -> None:
    """Opt-in disablement (enabled=False) safely skips persistence."""
    al = AuditLogger(sqlite_store, TEST_SIGNING_KEY, enable_async=False, enabled=False)
    await al.log(_make_event())
    assert await sqlite_store.count() == 0


# ---------------------------------------------------------------------------
# 5. RBAC Audit Search & Compliance API
# ---------------------------------------------------------------------------


@pytest.fixture
def compliance_api_client(tmp_path: Path) -> tuple[TestClient, AuditLogger]:
    store = SQLiteAuditStore(tmp_path / "api_audit.db")
    al = AuditLogger(store, TEST_SIGNING_KEY, enable_async=False)
    gdpr_store = InMemoryGDPRDataStore()
    gdpr_store.seed_user(
        "regular_user",
        profile={"user_id": "regular_user", "email": "reg@example.com", "full_name": "Reg User"},
        agent_executions=[{"execution_id": "e1", "agent": "discovery"}],
        model_calls=[{"call_id": "c1", "model": "gpt-4o", "prompt": "private prompt"}],
    )
    gdpr = GDPRHandler(al, gdpr_store)
    principals = {
        "sec-key-1": AuditPrincipal(
            user_id="sec_analyst",
            role="security_admin",
            permissions=frozenset({"audit:read", "compliance:admin"}),
        ),
        "user-key-1": AuditPrincipal(
            user_id="regular_user",
            role="user",
            permissions=frozenset({"audit:self"}),
        ),
        "no-perm-key": AuditPrincipal(
            user_id="guest_user",
            role="guest",
            permissions=frozenset(),
        ),
    }
    app = create_app(
        audit_logger=al,
        gdpr_handler=gdpr,
        audit_principals=principals,
        gdpr_export_dir=tmp_path / "exports",
        audit_rate_limit_per_hour=20,
    )
    client = TestClient(app)
    yield client, al
    store.close()


@pytest.mark.unit
def test_audit_api_enforces_authentication_and_owasp_headers(
    compliance_api_client: tuple[TestClient, AuditLogger],
) -> None:
    """Unauthenticated requests return 401, include OWASP headers, and log USER_LOGIN_FAILED."""
    client, al = compliance_api_client
    resp = client.get("/api/v1/audit/search")
    assert resp.status_code == 401
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"

    failed = asyncio.run(al.search(event_type=AuditEventType.USER_LOGIN_FAILED))
    assert len(failed) == 1


@pytest.mark.unit
def test_audit_api_rbac_enforces_audit_read_and_self_scoping(
    compliance_api_client: tuple[TestClient, AuditLogger],
) -> None:
    """Security admins query all logs; regular users can only query their own; guests get 403."""
    client, al = compliance_api_client
    asyncio.run(al.log(_make_event(user_id="regular_user", subject_id="regular_user")))
    asyncio.run(al.log(_make_event(user_id="other_user", subject_id="other_user")))

    # Guest without permissions -> 403
    r_guest = client.get("/api/v1/audit/search", headers={"X-API-Key": "no-perm-key"})
    assert r_guest.status_code == 403

    # Regular user querying another user -> 403
    r_cross = client.get(
        "/api/v1/audit/search?user_id=other_user",
        headers={"X-API-Key": "user-key-1"},
    )
    assert r_cross.status_code == 403

    # Regular user querying own events -> 200 scoped to regular_user
    r_self = client.get("/api/v1/audit/search", headers={"X-API-Key": "user-key-1"})
    assert r_self.status_code == 200
    assert r_self.json()["count"] == 1
    assert r_self.json()["integrity_verified"] is True

    # Security analyst with audit:read -> 200 sees all events
    r_sec = client.get(
        "/api/v1/audit/search",
        headers={"Authorization": "Bearer sec-key-1"},
    )
    assert r_sec.status_code == 200
    assert r_sec.json()["count"] >= 2
    assert r_sec.json()["integrity_verified"] is True


@pytest.mark.unit
def test_audit_api_export_csv_and_verify_endpoints(
    compliance_api_client: tuple[TestClient, AuditLogger],
) -> None:
    """Security team can export CSV evidence and run HMAC verification sweep."""
    client, al = compliance_api_client
    asyncio.run(al.log(_make_event(user_id="alice", action="Exported KPI report")))

    # Regular user cannot call /export or /verify
    r_exp = client.get("/api/v1/audit/export", headers={"X-API-Key": "user-key-1"})
    r_ver = client.post("/api/v1/audit/verify", headers={"X-API-Key": "user-key-1"})
    assert r_exp.status_code == 403
    assert r_ver.status_code == 403

    csv_resp = client.get(
        "/api/v1/audit/export?export_format=csv",
        headers={"X-API-Key": "sec-key-1"},
    )
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "signature_valid" in csv_resp.text

    verify_resp = client.post("/api/v1/audit/verify", headers={"X-API-Key": "sec-key-1"})
    assert verify_resp.status_code == 200
    assert verify_resp.json()["integrity_verified"] is True
    assert verify_resp.json()["tampered"] == 0


@pytest.mark.unit
def test_audit_api_rate_limiting_returns_429(tmp_path: Path) -> None:
    """Rate limiter enforces hourly quota and returns HTTP 429 with Retry-After."""
    store = SQLiteAuditStore(tmp_path / "rl.db")
    al = AuditLogger(store, TEST_SIGNING_KEY, enable_async=False)
    app = create_app(
        audit_logger=al,
        audit_principals={
            "k1": AuditPrincipal("sec1", "security_admin", frozenset({"audit:read"}))
        },
        audit_rate_limit_per_hour=2,
    )
    client = TestClient(app)
    assert client.get("/api/v1/audit/search", headers={"X-API-Key": "k1"}).status_code == 200
    assert client.get("/api/v1/audit/search", headers={"X-API-Key": "k1"}).status_code == 200
    r3 = client.get("/api/v1/audit/search", headers={"X-API-Key": "k1"})
    assert r3.status_code == 429
    assert "Retry-After" in r3.headers
    store.close()


@pytest.mark.unit
def test_gdpr_api_endpoints_enforce_subject_ownership(
    compliance_api_client: tuple[TestClient, AuditLogger],
) -> None:
    """Data subjects can export/delete their own data but cannot access other subjects."""
    client, _ = compliance_api_client

    # Regular user attempting to export another user -> 403
    r_forbidden = client.post(
        "/api/v1/compliance/gdpr/export",
        headers={"X-API-Key": "user-key-1"},
        json={"user_id": "someone_else", "requester_email": "x@example.com"},
    )
    assert r_forbidden.status_code == 403

    # Regular user exporting own data -> 200
    r_own = client.post(
        "/api/v1/compliance/gdpr/export",
        headers={"X-API-Key": "user-key-1"},
        json={"user_id": "regular_user", "requester_email": "reg@example.com"},
    )
    assert r_own.status_code == 200
    assert r_own.json()["archive_name"].endswith(".zip")

    # Regular user cannot pass retain_audit_logs=False
    r_strip = client.post(
        "/api/v1/compliance/gdpr/delete",
        headers={"X-API-Key": "user-key-1"},
        json={
            "user_id": "regular_user",
            "reason": "Delete everything",
            "retain_audit_logs": False,
        },
    )
    assert r_strip.status_code == 403


# ---------------------------------------------------------------------------
# 6. Quarterly Access Review Automation (SOC2 CC6.2 / CC6.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quarterly_access_review_flags_dormant_and_no_mfa_privileged_accounts(
    audit_logger: AuditLogger,
) -> None:
    """QuarterlyAccessReviewer identifies dormant and non-MFA privileged accounts."""
    now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    accounts = [
        AccessRecord(
            user_id="active_admin",
            role="security_admin",
            permissions=("audit:read",),
            last_active_at=now - timedelta(days=5),
            mfa_enabled=True,
        ),
        AccessRecord(
            user_id="stale_admin",
            role="admin",
            permissions=("compliance:admin",),
            last_active_at=now - timedelta(days=120),
            mfa_enabled=False,
        ),
        AccessRecord(
            user_id="dormant_user",
            role="user",
            permissions=(),
            last_active_at=now - timedelta(days=100),
            mfa_enabled=True,
        ),
    ]

    reviewer = QuarterlyAccessReviewer(inactive_days_threshold=90)
    report = await reviewer.generate_review_report(
        accounts,
        now=now,
        review_quarter="2026-Q4",
        audit_logger=audit_logger,
    )

    assert report.review_quarter == "2026-Q4"
    assert report.total_accounts_reviewed == 3
    assert report.privileged_accounts == 2
    assert report.approved_accounts == ["active_admin"]
    assert report.passed is False
    categories = {(f.user_id, f.category, f.severity) for f in report.findings}
    assert ("stale_admin", "privileged_account_no_mfa", "high") in categories
    assert ("stale_admin", "dormant_account", "high") in categories
    assert ("dormant_user", "dormant_account", "medium") in categories

    md = report.to_markdown()
    assert "2026-Q4" in md
    assert "stale_admin" in md

    # Review completion logged as CONFIG_CHANGED audit event
    review_events = await audit_logger.search(event_type=AuditEventType.CONFIG_CHANGED)
    assert len(review_events) == 1


# ---------------------------------------------------------------------------
# 7. Config Contract, Migrations & Additive Runtime Instrumentation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compliance_yaml_contract_and_migrations(tmp_path: Path) -> None:
    """config/compliance.yaml validates strictly and SQLite/PostgreSQL migrations are valid."""
    cfg = validate_config_file(REPO_ROOT / "config" / "compliance.yaml")
    assert isinstance(cfg, ComplianceConfigFile)
    assert cfg.compliance.audit.enabled is True
    assert cfg.compliance.retention.hot_storage_days == 90
    assert cfg.compliance.retention.cold_storage_days == 2555
    assert cfg.compliance.s3.storage_class == "GLACIER"
    assert cfg.compliance.s3.encryption == "AES256"

    # Run real SQLite migrations including 002_create_audit_tables.sql
    db = Database(tmp_path / "migrated.db", REPO_ROOT / "migrations")
    db.connect()
    db.migrate()
    cols = {
        row["name"]
        for row in db.execute("PRAGMA table_info(audit_logs)").fetchall()
    }
    assert {
        "id",
        "event_id",
        "event_type",
        "timestamp",
        "user_id",
        "subject_id",
        "action",
        "resource_type",
        "resource_id",
        "result",
        "metadata",
        "client_ip",
        "user_agent",
        "session_id",
        "signature",
    }.issubset(cols)
    indexes = {
        row["name"]
        for row in db.execute("PRAGMA index_list(audit_logs)").fetchall()
    }
    assert {
        "idx_audit_user_timestamp",
        "idx_audit_subject_timestamp",
        "idx_audit_type_timestamp",
        "idx_audit_session",
    }.issubset(indexes)
    db.close()

    # Verify PostgreSQL migration defines JSONB, composite indexes & REVOKE DELETE, UPDATE
    pg_sql = (REPO_ROOT / "migrations" / "postgres" / "002_create_audit_tables.sql").read_text(
        encoding="utf-8"
    )
    assert "JSONB" in pg_sql
    assert "REVOKE DELETE, UPDATE, TRUNCATE ON audit_logs FROM pvs_user" in pg_sql
    assert "idx_audit_user_timestamp" in pg_sql
    assert "idx_audit_subject_timestamp" in pg_sql


@pytest.mark.asyncio
async def test_additive_runtime_instrumentation_captures_agent_and_model_calls(
    sqlite_store: SQLiteAuditStore,
) -> None:
    """When global AuditLogger is initialized, BaseAgent and Harness emit audit events."""

    class _DummyAgent(BaseAgent):
        async def _execute_impl(
            self,
            input_artifact: dict[str, Any],
            harness: Harness,
            context: ExecutionContext,
        ) -> AgentResult:
            await harness.invoke_model([{"role": "user", "content": "Summarize KPI status"}])
            return AgentResult(
                success=True,
                output_artifact={"summary": "ok"},
                iterations=1,
                cost_usd=0.0,
                duration_seconds=0.01,
                reasoning_trace=[],
                tool_calls=[],
                checkpoints=[],
            )

    try:
        al = init_audit_logger(sqlite_store, TEST_SIGNING_KEY, enable_async=False)
        assert get_audit_logger() is al

        agent = _DummyAgent(
            name="audit_smoke_agent",
            version="1.0.0",
            capabilities=["test"],
            required_tools=[],
            input_schema="none",
            output_schema="none",
        )
        ctx = ExecutionContext(run_id="run-audit-01", agent_name="audit_smoke_agent")
        harness = Harness(context=ctx)
        res = await agent.execute({"ping": "pong"}, harness, ctx)
        assert res.success is True

        events = await al.search(session_id="run-audit-01", limit=20)
        types = {ev.event_type for ev in events}
        assert AuditEventType.AGENT_STARTED in types
        assert AuditEventType.MODEL_CALLED in types
        assert AuditEventType.AGENT_COMPLETED in types
        assert all(al.verify_signature(ev) for ev in events)
    finally:
        reset_audit_logger()


@pytest.mark.unit
def test_audit_event_round_trip_serialization_and_utc_normalization() -> None:
    """AuditEvent normalizes naive timestamps to UTC and round-trips through dict cleanly."""
    naive = datetime(2026, 11, 23, 14, 0, 0)
    ev = _make_event(timestamp=naive, metadata={"k": "v"})
    assert ev.timestamp.tzinfo is not None
    rebuilt = AuditEvent.from_dict(ev.to_dict())
    assert rebuilt.event_type == ev.event_type
    assert rebuilt.timestamp == ev.timestamp
    assert rebuilt.metadata == {"k": "v"}


@pytest.mark.unit
def test_signer_rejects_empty_key_and_reports_non_sensitive_fingerprint() -> None:
    """AuditSigner rejects empty keys and exposes a 16-char SHA-256 fingerprint."""
    with pytest.raises(ValueError, match="empty"):
        AuditSigner(b"")
    signer = AuditSigner(TEST_SIGNING_KEY)
    assert len(signer.key_fingerprint) == 16
    assert signer.key_fingerprint != TEST_SIGNING_KEY.decode("utf-8")


@pytest.mark.asyncio
async def test_verify_events_detects_tampered_rows_in_batch(audit_logger: AuditLogger) -> None:
    """verify_events reports exact count and event_ids of tampered records."""
    e1 = await audit_logger.log(_make_event(user_id="u1"))
    e2 = await audit_logger.log(_make_event(user_id="u2"))
    e2.result = "forged"
    summary = audit_logger.verify_events([e1, e2])
    assert summary["total"] == 2
    assert summary["valid"] == 1
    assert summary["tampered"] == 1
    assert summary["tampered_event_ids"] == [e2.event_id]
    assert summary["integrity_verified"] is False


@pytest.mark.asyncio
async def test_postgres_audit_store_sql_contract_with_mock_pool() -> None:
    """PostgresAuditStore issues parameterized SQL for insert, query, prune, and count."""
    from product_value_system.compliance import PostgresAuditStore

    executed: list[tuple[str, tuple[Any, ...]]] = []

    class _FakeConn:
        async def execute(self, sql: str, *args: Any) -> str:
            executed.append((sql, args))
            return "DELETE 2"

        async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
            executed.append((sql, args))
            return []

        async def fetchval(self, sql: str) -> int:
            executed.append((sql, ()))
            return 7

    class _AcquireCtx:
        async def __aenter__(self) -> _FakeConn:
            return _FakeConn()

        async def __aexit__(self, *args: Any) -> None:
            return None

    class _FakePool:
        def acquire(self) -> _AcquireCtx:
            return _AcquireCtx()

    store = PostgresAuditStore(_FakePool())
    ev = _make_event()
    ev.event_id = "ev-pg-1"
    ev.signature = "a" * 64
    await store.insert(ev)
    assert await store.query(user_id="user123", event_type=AuditEventType.USER_LOGIN) == []
    assert await store.archive_and_prune(["ev-pg-1", "ev-pg-2"]) == 2
    assert await store.count() == 7
    assert any("INSERT INTO audit_logs" in sql for sql, _ in executed)


@pytest.mark.unit
def test_compliance_config_schema_rejects_invalid_retention_and_cron() -> None:
    """Strict ComplianceConfigFile rejects cold < hot storage days and invalid cron syntax."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ComplianceConfigFile.model_validate(
            {
                "compliance": {
                    "retention": {
                        "hot_storage_days": 180,
                        "cold_storage_days": 90,
                    }
                }
            }
        )

    with pytest.raises(ValidationError):
        ComplianceConfigFile.model_validate(
            {
                "compliance": {
                    "retention": {
                        "archival_schedule": "not-a-cron",
                    }
                }
            }
        )


@pytest.mark.unit
def test_alembic_migration_script_is_valid_python_with_revoke() -> None:
    """Alembic migration 001_create_audit_tables.py parses cleanly and revokes DELETE/UPDATE."""
    import ast

    mig = REPO_ROOT / "alembic" / "versions" / "001_create_audit_tables.py"
    source = mig.read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert {"upgrade", "downgrade"}.issubset(fn_names)
    assert "REVOKE DELETE, UPDATE, TRUNCATE ON audit_logs FROM pvs_user" in source


@pytest.mark.unit
def test_top_level_lazy_exports_include_compliance_symbols() -> None:
    """product_value_system lazily exports AuditLogger, GDPRHandler, and RetentionPolicy."""
    import product_value_system as pvs

    assert pvs.AuditLogger is AuditLogger
    assert pvs.AuditEvent is AuditEvent
    assert pvs.AuditEventType is AuditEventType
    assert pvs.GDPRHandler is GDPRHandler
    assert pvs.QuarterlyAccessReviewer is QuarterlyAccessReviewer

