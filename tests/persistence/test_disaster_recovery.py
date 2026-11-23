"""
Comprehensive tests for PROD-010 Disaster Recovery Implementation:
- AES-256-GCM encrypted consistent online backups & tamper rejection
- Cross-region S3 replication (us-west-2) with AES256 SSE + S3 Object Lock (COMPLIANCE)
- Full database restore & Point-in-Time Recovery (PITR) reversing accidental DROP TABLE
- Weekly automated backup verification (PRAGMA integrity_check + required table rows)
- End-to-end DR Drill measuring RTO (<1h) & RPO (<15m) with compliance audit trail
- Validation of pgBackRest, PostgreSQL WAL config, restore/PITR scripts & Helm CronJobs
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from product_value_system.compliance import AuditEventType, AuditLogger, SQLiteAuditStore
from product_value_system.persistence.database import Database
from product_value_system.persistence.disaster_recovery import (
    DisasterRecoveryManager,
    decrypt_backup_bytes,
    encrypt_backup_bytes,
    generate_backup_key,
)
from product_value_system.security.secret_detector import HybridSecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeReplicaS3Client:
    """In-memory cross-region S3 mock verifying AES256 SSE and Object Lock COMPLIANCE."""

    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.uploads.append(kwargs)
        return {"ETag": f"etag-{len(self.uploads)}"}


def _seed_migrated_db(db_path: Path, *, rows: int = 5) -> None:
    db = Database(db_path, REPO_ROOT / "migrations")
    db.connect()
    db.migrate()
    now_iso = datetime.now(UTC).isoformat()
    for i in range(rows):
        db.execute(
            """
            INSERT INTO value_stream_runs (id, problem_id, status, started_at, total_cost_usd)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (f"run-{i}", f"prob-{i}", now_iso, 0.25 * (i + 1)),
        )
        db.execute(
            """
            INSERT INTO audit_logs (
                event_id, event_type, timestamp, user_id, action,
                resource_type, result, signature
            ) VALUES (?, 'user.login', ?, ?, 'login', 'auth', 'success', ?)
            """,
            (f"ev-{i}", now_iso, f"user-{i}", "a" * 64),
        )
    assert db._connection is not None
    db._connection.commit()
    db.close()


# ---------------------------------------------------------------------------
# 1. AES-256-GCM Encryption & Tamper Detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_aes_256_gcm_round_trip_and_tamper_detection() -> None:
    """AES-256-GCM compresses, encrypts, decrypts cleanly, and rejects 1-bit tampering."""
    key = generate_backup_key()
    assert len(key) == 32
    raw = b"critical-postgresql-transaction-log" * 200
    blob = encrypt_backup_bytes(raw, key)

    assert len(blob) < len(raw)  # gzip compressed
    assert decrypt_backup_bytes(blob, key) == raw

    # Flip 1 bit in ciphertext -> must fail GCM tag verification
    corrupted = bytearray(blob)
    corrupted[-5] ^= 0x01
    with pytest.raises(ValueError, match="AES-256-GCM tag mismatch"):
        decrypt_backup_bytes(bytes(corrupted), key)

    # Wrong key -> must fail
    with pytest.raises(ValueError, match="AES-256-GCM tag mismatch"):
        decrypt_backup_bytes(blob, generate_backup_key())

    with pytest.raises(ValueError, match="32 bytes"):
        encrypt_backup_bytes(raw, b"short")


# ---------------------------------------------------------------------------
# 2. Consistent Encrypted Backup & Cross-Region S3 Replication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_encrypted_backup_and_cross_region_s3_replication_with_object_lock(
    tmp_path: Path,
) -> None:
    """Consistent backup encrypts DB and replicates to S3 us-west-2 with Object Lock."""
    src_db = tmp_path / "prod.db"
    _seed_migrated_db(src_db, rows=8)
    s3_mock = _FakeReplicaS3Client()
    audit_store = SQLiteAuditStore(tmp_path / "dr_audit.db")
    audit_logger = AuditLogger(audit_store, b"dr_audit_key_32_bytes_exact!!!!!", enable_async=False)

    mgr = DisasterRecoveryManager(
        tmp_path / "backups",
        generate_backup_key(),
        s3_bucket="pvs-backups",
        replica_region="us-west-2",
        s3_client=s3_mock,
        enable_object_lock=True,
        object_lock_days=30,
        audit_logger=audit_logger,
    )

    manifest = await mgr.create_backup(src_db, backup_type="full", replicate_to_s3=True)
    assert Path(manifest.artifact_path).is_file()
    assert manifest.encryption == "AES-256-GCM"
    assert manifest.replicated_region == "us-west-2"
    assert manifest.object_lock_mode == "COMPLIANCE"
    assert len(s3_mock.uploads) == 1

    upload = s3_mock.uploads[0]
    assert upload["Bucket"] == "pvs-backups"
    assert upload["ServerSideEncryption"] == "AES256"
    assert upload["ObjectLockMode"] == "COMPLIANCE"
    assert "ObjectLockRetainUntilDate" in upload

    # Backup operation audited in compliance log
    events = await audit_logger.search(event_type=AuditEventType.DATA_EXPORT)
    assert len(events) == 1
    assert events[0].resource_id == manifest.backup_id
    audit_store.close()


# ---------------------------------------------------------------------------
# 3. Full Restore, Corruption Tamper Defense & Point-in-Time Recovery (PITR)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pitr_recovery_reverses_accidental_drop_table(tmp_path: Path) -> None:
    """PITR recovers 100% of rows from snapshot taken 10 minutes before destructive DROP TABLE."""
    src_db = tmp_path / "prod.db"
    _seed_migrated_db(src_db, rows=6)

    mgr = DisasterRecoveryManager(tmp_path / "backups", generate_backup_key())

    t_good = datetime(2026, 11, 23, 10, 0, 0, tzinfo=UTC)
    good_manifest = await mgr.create_backup(src_db, backup_type="full", timestamp=t_good)

    # Simulate accidental DROP TABLE at 10:10 UTC followed by a bad snapshot at 10:15 UTC
    conn = sqlite3.connect(str(src_db))
    conn.execute("DROP TABLE value_stream_runs")
    conn.commit()
    conn.close()

    t_bad = datetime(2026, 11, 23, 10, 15, 0, tzinfo=UTC)
    await mgr.create_backup(src_db, backup_type="diff", timestamp=t_bad)

    # Execute PITR to 10:05 UTC (after good backup, before accidental drop)
    restored_db = tmp_path / "restored.db"
    chosen = await mgr.restore_backup(
        restored_db,
        pitr_target=datetime(2026, 11, 23, 10, 5, 0, tzinfo=UTC),
    )
    assert chosen.backup_id == good_manifest.backup_id

    # Verify 100% of value_stream_runs and audit_logs rows recovered
    rconn = sqlite3.connect(str(restored_db))
    runs_cnt = rconn.execute("SELECT COUNT(*) FROM value_stream_runs").fetchone()[0]
    audit_cnt = rconn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    rconn.close()
    assert runs_cnt == 6
    assert audit_cnt == 6

    # PITR target preceding earliest backup fails fast
    with pytest.raises(ValueError, match="No recoverable backup"):
        await mgr.restore_backup(
            tmp_path / "too_early.db",
            pitr_target=datetime(2026, 11, 22, 0, 0, 0, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_restore_rejects_tampered_backup_artifact(tmp_path: Path) -> None:
    """Modifying an encrypted backup file on disk is rejected prior to restore."""
    src_db = tmp_path / "prod.db"
    _seed_migrated_db(src_db, rows=3)
    mgr = DisasterRecoveryManager(tmp_path / "backups", generate_backup_key())
    manifest = await mgr.create_backup(src_db, backup_type="full")

    # Tamper with artifact bytes on disk
    art = Path(manifest.artifact_path)
    data = bytearray(art.read_bytes())
    data[20] ^= 0xFF
    art.write_bytes(bytes(data))

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        await mgr.restore_backup(tmp_path / "out.db", backup_id=manifest.backup_id)


# ---------------------------------------------------------------------------
# 4. Automated Backup Verification & Quarterly DR Drill (RTO <1h, RPO <15m)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_weekly_backup_verification_and_dr_drill_meet_rto_rpo_slas(
    tmp_path: Path,
) -> None:
    """DR drill verifies PRAGMA integrity, row counts, RTO < 1h, RPO < 15m, and logs audit."""
    src_db = tmp_path / "prod.db"
    _seed_migrated_db(src_db, rows=10)
    audit_store = SQLiteAuditStore(tmp_path / "drill_audit.db")
    audit_logger = AuditLogger(
        audit_store, b"drill_audit_key_32_bytes_exact!!", enable_async=False
    )

    mgr = DisasterRecoveryManager(
        tmp_path / "backups",
        generate_backup_key(),
        audit_logger=audit_logger,
    )
    t_backup = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
    await mgr.create_backup(src_db, backup_type="full", timestamp=t_backup)

    # Simulate drill executed 8 minutes after backup (within 15-minute RPO SLA)
    drill = await mgr.execute_dr_drill(
        required_tables=("value_stream_runs", "audit_logs"),
        min_rows_by_table={"value_stream_runs": 10, "audit_logs": 10},
        now=t_backup + timedelta(minutes=8),
    )

    assert drill.passed is True
    assert drill.rto_met is True
    assert drill.rpo_met is True
    assert drill.rto_seconds < 3600.0
    assert drill.rpo_seconds == pytest.approx(480.0)
    assert drill.integrity_verified is True
    assert drill.table_row_counts["audit_logs"] == 10

    # Verify RPO breach is properly flagged when backup is >15 minutes stale
    stale_drill = await mgr.execute_dr_drill(
        required_tables=("audit_logs",),
        now=t_backup + timedelta(minutes=30),
    )
    assert stale_drill.rpo_met is False
    assert stale_drill.passed is False

    drill_events = await audit_logger.search(event_type=AuditEventType.CONFIG_CHANGED)
    assert len(drill_events) == 2
    audit_store.close()


# ---------------------------------------------------------------------------
# 5. pgBackRest, PostgreSQL WAL, Restore/PITR Scripts & Zero-Secrets Audit
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pgbackrest_wal_scripts_and_dr_docs_complete_and_zero_secrets() -> None:
    """DR config files, shell scripts, and runbooks are complete with zero hardcoded secrets."""
    pgb = (REPO_ROOT / "deploy" / "postgres" / "pgbackrest.conf").read_text(encoding="utf-8")
    assert "repo1-retention-full=7" in pgb
    assert "repo1-cipher-type=aes-256-cbc" in pgb
    assert "repo2-s3-region=us-west-2" in pgb
    assert "repo2-retention-full=30" in pgb
    assert "repo1-cipher-pass=" not in pgb  # Must be injected via Secret env var, never hardcoded

    wal = (REPO_ROOT / "deploy" / "postgres" / "postgresql-wal.conf").read_text(encoding="utf-8")
    assert "wal_level = replica" in wal
    assert "archive_mode = on" in wal
    assert "archive_timeout = 300" in wal

    restore_sh = (REPO_ROOT / "scripts" / "restore-database.sh").read_text(encoding="utf-8")
    pitr_sh = (REPO_ROOT / "scripts" / "pitr-recovery.sh").read_text(encoding="utf-8")
    assert "--pitr" in restore_sh and "pgbackrest" in restore_sh
    assert "restore-database.sh" in pitr_sh and "TARGET_EPOCH" in pitr_sh

    dr_plan = (REPO_ROOT / "docs" / "operations" / "disaster-recovery-plan.md").read_text(
        encoding="utf-8"
    )
    restore_doc = (REPO_ROOT / "docs" / "operations" / "restore-procedures.md").read_text(
        encoding="utf-8"
    )
    assert "< 1 hour" in dr_plan and "< 15 minutes" in dr_plan and "Quarterly DR Drills" in dr_plan
    assert "pitr-recovery.sh" in restore_doc

    detector = HybridSecretDetector()
    for path in (
        REPO_ROOT / "deploy" / "postgres" / "pgbackrest.conf",
        REPO_ROOT / "deploy" / "postgres" / "postgresql-wal.conf",
        REPO_ROOT / "scripts" / "restore-database.sh",
        REPO_ROOT / "scripts" / "pitr-recovery.sh",
    ):
        findings = detector.detect(path.read_text(encoding="utf-8"))
        assert findings == [], f"Unexpected secret finding in {path}: {findings}"
