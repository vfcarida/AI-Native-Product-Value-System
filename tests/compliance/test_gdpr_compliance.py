"""
GDPR Data Subject Rights (Articles 15 & 17) and S3 Glacier Retention Policy tests (PROD-006).
"""

from __future__ import annotations

import gzip
import json
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from product_value_system.compliance import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
    GDPRHandler,
    RetentionPolicy,
    SQLiteAuditStore,
    SQLiteGDPRDataStore,
)

TEST_SIGNING_KEY = b"gdpr_test_signing_key_32_bytes!!"


class _FakeS3Paginator:
    def __init__(self, objects: list[dict[str, Any]]) -> None:
        self._objects = objects

    def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
        bucket = str(kwargs["Bucket"])
        prefix = str(kwargs.get("Prefix", ""))
        matched = [
            obj
            for obj in self._objects
            if obj["Bucket"] == bucket and obj["Key"].startswith(prefix)
        ]
        return [{"Contents": matched}] if matched else [{}]


class _FakeS3Client:
    """In-memory S3 mock validating Glacier StorageClass, AES256 SSE, and deletions."""

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.objects: list[dict[str, Any]] = []
        self.deleted_keys: list[str] = []
        self._fail_on_call = fail_on_call
        self._call_count = 0

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self._call_count += 1
        if self._fail_on_call is not None and self._call_count == self._fail_on_call:
            raise RuntimeError("Simulated S3 upload failure")
        self.uploads.append(kwargs)
        self.objects.append(
            {
                "Bucket": kwargs["Bucket"],
                "Key": kwargs["Key"],
                "Body": kwargs["Body"],
                "LastModified": datetime.now(UTC),
            }
        )
        return {"ETag": f"etag-{self._call_count}"}

    def get_paginator(self, name: str) -> _FakeS3Paginator:
        assert name == "list_objects_v2"
        return _FakeS3Paginator(self.objects)

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        bucket = str(kwargs["Bucket"])
        key = str(kwargs["Key"])
        self.deleted_keys.append(key)
        self.objects = [
            o for o in self.objects if not (o["Bucket"] == bucket and o["Key"] == key)
        ]
        return {}


@pytest.fixture
def compliance_env(
    tmp_path: Path,
) -> tuple[SQLiteAuditStore, AuditLogger, SQLiteGDPRDataStore, GDPRHandler]:
    audit_store = SQLiteAuditStore(tmp_path / "audit.db")
    audit_logger = AuditLogger(audit_store, TEST_SIGNING_KEY, enable_async=False)
    gdpr_store = SQLiteGDPRDataStore(tmp_path / "gdpr.db")
    handler = GDPRHandler(audit_logger, gdpr_store)
    yield audit_store, audit_logger, gdpr_store, handler
    audit_store.close()
    gdpr_store.close()


# ---------------------------------------------------------------------------
# 1. GDPR Article 15 — Right of Access (DSAR Export)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gdpr_data_export_generates_complete_zip_archive(
    compliance_env: tuple[SQLiteAuditStore, AuditLogger, SQLiteGDPRDataStore, GDPRHandler],
    tmp_path: Path,
) -> None:
    """GDPR Article 15 export creates ZIP with user_data.json, README.txt, and redacted prompts."""
    _, audit_logger, gdpr_store, gdpr_handler = compliance_env
    user_id = "gdpr_test_user"

    gdpr_store.upsert_user_profile(
        user_id,
        email="alice@example.com",
        full_name="Alice Compliance",
        metadata={"tier": "enterprise", "api_key": "placeholder-should-redact"},
    )
    gdpr_store.record_agent_execution("exec-1", user_id, "discovery_agent", cost_usd=0.02)
    gdpr_store.record_model_call(
        "call-1",
        user_id,
        "openai",
        "gpt-4o",
        prompt="Confidential customer prompt text",
        input_tokens=120,
        output_tokens=60,
        cost_usd=0.005,
    )
    await audit_logger.log(
        AuditEvent(
            event_type=AuditEventType.USER_LOGIN,
            timestamp=datetime.now(UTC),
            user_id=user_id,
            subject_id=user_id,
            action="User signed in",
            resource_type="auth",
            resource_id=None,
            result="success",
            metadata={},
        )
    )

    export_path = await gdpr_handler.export_user_data(
        user_id=user_id,
        requester_email="alice@example.com",
        output_dir=tmp_path,
    )

    assert export_path.exists()
    assert export_path.suffix == ".zip"

    with zipfile.ZipFile(export_path, "r") as zipf:
        files = set(zipf.namelist())
        assert {"user_data.json", "README.txt"}.issubset(files)

        user_data = json.loads(zipf.read("user_data.json").decode("utf-8"))
        assert user_data["profile"]["email"] == "alice@example.com"
        assert user_data["profile"]["metadata"]["api_key"] == "[REDACTED]"
        assert len(user_data["agent_executions"]) == 1
        assert len(user_data["model_calls"]) == 1
        assert user_data["model_calls"][0]["prompt"].startswith("[REDACTED_PROMPT")
        assert "Confidential" not in user_data["model_calls"][0]["prompt"]
        assert len(user_data["audit_logs"]) >= 2
        assert user_data["metadata"]["user_id"] == user_id
        assert "Article 15" in user_data["metadata"]["gdpr_article"]

        readme = zipf.read("README.txt").decode("utf-8")
        assert "GDPR Data Export" in readme

    # Verify both GDPR_EXPORT_REQUESTED and DATA_EXPORT events were recorded
    export_req = await audit_logger.search(event_type=AuditEventType.GDPR_EXPORT_REQUESTED)
    export_done = await audit_logger.search(event_type=AuditEventType.DATA_EXPORT)
    assert len(export_req) == 1
    assert len(export_done) == 1


@pytest.mark.asyncio
async def test_gdpr_export_csv_format_and_path_traversal_defense(
    compliance_env: tuple[SQLiteAuditStore, AuditLogger, SQLiteGDPRDataStore, GDPRHandler],
    tmp_path: Path,
) -> None:
    """CSV export includes audit_logs.csv and sanitizes path-traversal user IDs safely."""
    _, _, _, gdpr_handler = compliance_env
    export_dir = tmp_path / "exports"
    export_path = await gdpr_handler.export_user_data(
        user_id="../../etc/passwd",
        requester_email="user@example.com",
        output_dir=export_dir,
        export_format="csv",
    )
    assert export_dir.resolve() in export_path.parents
    with zipfile.ZipFile(export_path, "r") as zipf:
        assert "audit_logs.csv" in zipf.namelist()


@pytest.mark.asyncio
async def test_gdpr_export_completes_well_within_sla(
    compliance_env: tuple[SQLiteAuditStore, AuditLogger, SQLiteGDPRDataStore, GDPRHandler],
    tmp_path: Path,
) -> None:
    """GDPR export executes well within the 30-day SLA (<10s in local verification)."""
    _, _, _, gdpr_handler = compliance_env
    start = time.monotonic()
    export_path = await gdpr_handler.export_user_data(
        user_id="sla_test_user",
        requester_email="sla@example.com",
        output_dir=tmp_path,
    )
    elapsed = time.monotonic() - start
    assert elapsed < 10.0
    assert export_path.exists()


# ---------------------------------------------------------------------------
# 2. GDPR Article 17 — Right to Erasure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gdpr_data_deletion_anonymizes_profile_and_retains_audit_logs(
    compliance_env: tuple[SQLiteAuditStore, AuditLogger, SQLiteGDPRDataStore, GDPRHandler],
) -> None:
    """Article 17 deletion anonymizes profile, deletes operational rows, and retains audit logs."""
    _, audit_logger, gdpr_store, gdpr_handler = compliance_env
    user_id = "deletion_test_user"

    gdpr_store.upsert_user_profile(user_id, email="delete@example.com", full_name="Delete Me")
    gdpr_store.record_agent_execution("exec-d1", user_id, "build_agent")
    gdpr_store.record_agent_execution("exec-d2", user_id, "spec_agent")
    gdpr_store.record_model_call("call-d1", user_id, "anthropic", "claude-3-5-sonnet")
    await audit_logger.log(
        AuditEvent(
            event_type=AuditEventType.DATA_WRITE,
            timestamp=datetime.now(UTC),
            user_id=user_id,
            subject_id=user_id,
            action="Created draft spec",
            resource_type="spec",
            resource_id="spec-1",
            result="success",
            metadata={},
        )
    )

    counts = await gdpr_handler.delete_user_data(
        user_id=user_id,
        reason="User requested account deletion",
        retain_audit_logs=True,
    )

    assert counts == {
        "profile": 1,
        "agent_executions": 2,
        "model_calls": 1,
        "audit_logs": 0,  # Retained under GDPR Art. 17(3)(b)
    }

    profile = await gdpr_store.get_user_profile(user_id)
    assert profile is not None
    assert profile["email"] is None
    assert profile["full_name"] is None
    assert profile["anonymized_at"] is not None
    assert await gdpr_store.get_agent_executions(user_id) == []
    assert await gdpr_store.get_model_calls(user_id) == []

    # Audit logs retained + deletion lifecycle audited
    retained = await audit_logger.search(subject_id=user_id)
    types = {e.event_type for e in retained}
    assert AuditEventType.DATA_WRITE in types
    assert AuditEventType.GDPR_DELETION_REQUESTED in types
    assert AuditEventType.DATA_DELETE in types


@pytest.mark.asyncio
async def test_gdpr_data_deletion_optional_audit_log_anonymization(
    compliance_env: tuple[SQLiteAuditStore, AuditLogger, SQLiteGDPRDataStore, GDPRHandler],
) -> None:
    """When retain_audit_logs=False, subject identifiers inside audit_logs are anonymized."""
    _, audit_logger, gdpr_store, gdpr_handler = compliance_env
    user_id = "erasure_strict_user"

    gdpr_store.upsert_user_profile(user_id, email="strict@example.com", full_name="Strict User")
    await audit_logger.log(
        AuditEvent(
            event_type=AuditEventType.USER_LOGIN,
            timestamp=datetime.now(UTC),
            user_id=user_id,
            subject_id=user_id,
            action="Logged in",
            resource_type="auth",
            resource_id=None,
            result="success",
            metadata={},
            client_ip="203.0.113.10",
            user_agent="Mozilla/5.0",
        )
    )

    counts = await gdpr_handler.delete_user_data(
        user_id=user_id,
        reason="Court-ordered full PII erasure",
        retain_audit_logs=False,
    )
    assert counts["audit_logs"] >= 2
    assert await audit_logger.search(subject_id=user_id) == []
    anonymized = await audit_logger.search(subject_id="[ANONYMIZED]")
    assert len(anonymized) >= 2
    assert all(ev.client_ip is None for ev in anonymized)


# ---------------------------------------------------------------------------
# 3. Retention Policy — 90-Day S3 Glacier Archival & 7-Year Expiration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_policy_archives_old_logs_to_s3_glacier_with_high_compression(
    compliance_env: tuple[SQLiteAuditStore, AuditLogger, SQLiteGDPRDataStore, GDPRHandler],
) -> None:
    """Logs >90 days are gzip-compressed (>80% savings), uploaded to Glacier AES-256, and pruned."""
    audit_store, audit_logger, _, _ = compliance_env
    fake_s3 = _FakeS3Client()

    old_ts = datetime.now(UTC) - timedelta(days=100)
    recent_ts = datetime.now(UTC) - timedelta(days=10)

    # Seed 60 old logs (repetitive JSON compresses well >80%) + 5 recent logs
    for i in range(60):
        await audit_logger.log(
            AuditEvent(
                event_type=AuditEventType.MODEL_CALLED,
                timestamp=old_ts + timedelta(seconds=i),
                user_id=f"archive_user_{i % 5}",
                subject_id=None,
                action="Invoked foundation model for product specification analysis",
                resource_type="model",
                resource_id="gpt-4o",
                result="success",
                metadata={"tokens": 1200, "stage": "specification", "batch": "historical"},
                client_ip="10.0.1.15",
                session_id=f"sess_hist_{i % 5}",
            )
        )
    for i in range(5):
        await audit_logger.log(
            AuditEvent(
                event_type=AuditEventType.USER_LOGIN,
                timestamp=recent_ts + timedelta(seconds=i),
                user_id=f"recent_user_{i}",
                subject_id=None,
                action="Recent login",
                resource_type="auth",
                resource_id=None,
                result="success",
                metadata={},
            )
        )

    policy = RetentionPolicy(
        audit_store,
        s3_bucket="pvs-audit-archive",
        hot_storage_days=90,
        cold_storage_days=2555,
        s3_client=fake_s3,
        audit_logger=audit_logger,
    )

    stats = await policy.archive_old_logs()
    assert stats["logs_archived"] == 60
    assert stats["errors"] == 0
    assert stats["objects_uploaded"] == 1
    assert stats["bytes_transferred"] > 0
    assert stats["compression_ratio_pct"] >= 80.0

    # Verify S3 Glacier + AES-256 encryption + valid gzip JSON payload
    upload = fake_s3.uploads[0]
    assert upload["Bucket"] == "pvs-audit-archive"
    assert upload["StorageClass"] == "GLACIER"
    assert upload["ServerSideEncryption"] == "AES256"
    assert upload["Key"].startswith(f"audit_logs/year={old_ts.year}/month={old_ts.month:02d}/")
    assert upload["Key"].endswith(".json.gz")

    decompressed = json.loads(gzip.decompress(upload["Body"]).decode("utf-8"))
    assert len(decompressed) == 60
    assert all("signature" in row for row in decompressed)

    # Verify old rows were pruned from hot DB while recent 5 + 1 archival meta-audit event remain
    remaining_old = await audit_store.query_older_than(datetime.now(UTC) - timedelta(days=90))
    assert remaining_old == []
    assert await audit_store.count() == 6


@pytest.mark.asyncio
async def test_retention_policy_preserves_db_rows_when_s3_upload_fails(
    compliance_env: tuple[SQLiteAuditStore, AuditLogger, SQLiteGDPRDataStore, GDPRHandler],
) -> None:
    """Failed S3 uploads never prune hot database rows (zero data loss)."""
    audit_store, audit_logger, _, _ = compliance_env
    failing_s3 = _FakeS3Client(fail_on_call=1)

    old_ts = datetime.now(UTC) - timedelta(days=120)
    await audit_logger.log(
        AuditEvent(
            event_type=AuditEventType.DATA_READ,
            timestamp=old_ts,
            user_id="u1",
            subject_id=None,
            action="Historical read",
            resource_type="data",
            resource_id="d1",
            result="success",
            metadata={},
        )
    )

    policy = RetentionPolicy(audit_store, s3_bucket="pvs-audit-archive", s3_client=failing_s3)
    stats = await policy.archive_old_logs()
    assert stats["errors"] == 1
    assert stats["logs_archived"] == 0
    assert await audit_store.count() == 1


@pytest.mark.asyncio
async def test_retention_policy_deletes_expired_cold_s3_objects_after_7_years(
    compliance_env: tuple[SQLiteAuditStore, AuditLogger, SQLiteGDPRDataStore, GDPRHandler],
) -> None:
    """Objects older than cold_storage_days (2,555 days / 7 years) are deleted from S3."""
    audit_store, audit_logger, _, _ = compliance_env
    fake_s3 = _FakeS3Client()
    now = datetime(2026, 11, 23, 3, 0, tzinfo=UTC)

    fake_s3.objects = [
        {
            "Bucket": "pvs-audit-archive",
            "Key": "audit_logs/year=2018/month=01/batch_0000.json.gz",
            "LastModified": now - timedelta(days=3000),
        },
        {
            "Bucket": "pvs-audit-archive",
            "Key": "audit_logs/year=2019/month=05/batch_0001.json.gz",
            "LastModified": now - timedelta(days=2600),
        },
        {
            "Bucket": "pvs-audit-archive",
            "Key": "audit_logs/year=2025/month=11/batch_0002.json.gz",
            "LastModified": now - timedelta(days=365),
        },
    ]

    policy = RetentionPolicy(
        audit_store,
        s3_bucket="pvs-audit-archive",
        hot_storage_days=90,
        cold_storage_days=2555,
        s3_client=fake_s3,
        audit_logger=audit_logger,
    )

    deleted = await policy.delete_expired_logs(now=now)
    assert deleted == 2
    assert len(fake_s3.deleted_keys) == 2
    assert len(fake_s3.objects) == 1
    assert fake_s3.objects[0]["Key"].endswith("batch_0002.json.gz")

    # Expiration deletion is itself audited
    del_events = await audit_logger.search(event_type=AuditEventType.DATA_DELETE)
    assert len(del_events) == 1
    assert del_events[0].metadata["deleted_objects"] == 2


@pytest.mark.asyncio
async def test_retention_policy_dry_run_does_not_mutate_s3_or_db(
    compliance_env: tuple[SQLiteAuditStore, AuditLogger, SQLiteGDPRDataStore, GDPRHandler],
) -> None:
    """Dry-run mode calculates archival stats without uploading to S3 or pruning DB."""
    audit_store, audit_logger, _, _ = compliance_env
    fake_s3 = _FakeS3Client()
    await audit_logger.log(
        AuditEvent(
            event_type=AuditEventType.USER_LOGIN,
            timestamp=datetime.now(UTC) - timedelta(days=120),
            user_id="dry_user",
            subject_id=None,
            action="Old login",
            resource_type="auth",
            resource_id=None,
            result="success",
            metadata={},
        )
    )
    policy = RetentionPolicy(audit_store, s3_bucket="pvs-audit-archive", s3_client=fake_s3)
    stats = await policy.archive_old_logs(dry_run=True)
    assert stats["dry_run"] is True
    assert stats["logs_archived"] == 1
    assert fake_s3.uploads == []
    assert await audit_store.count() == 1


@pytest.mark.unit
def test_retention_policy_rejects_invalid_day_bounds(
    compliance_env: tuple[SQLiteAuditStore, AuditLogger, SQLiteGDPRDataStore, GDPRHandler],
) -> None:
    """RetentionPolicy fails fast if hot_storage_days < 1 or cold < hot."""
    audit_store, _, _, _ = compliance_env
    with pytest.raises(ValueError, match="hot_storage_days"):
        RetentionPolicy(audit_store, "bucket", hot_storage_days=0)
    with pytest.raises(ValueError, match="cold_storage_days"):
        RetentionPolicy(audit_store, "bucket", hot_storage_days=120, cold_storage_days=90)


@pytest.mark.unit
def test_retention_policy_cli_dry_run_archive_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CronJob CLI entrypoint executes --mode archive --dry-run cleanly and outputs JSON."""
    from product_value_system.compliance.retention_policy import _cli_main

    db_path = tmp_path / "cli_audit.db"
    rc = _cli_main(
        [
            "--mode",
            "archive",
            "--db-path",
            str(db_path),
            "--s3-bucket",
            "pvs-audit-archive",
            "--dry-run",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert out["logs_archived"] == 0

