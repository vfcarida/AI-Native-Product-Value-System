"""
Disaster Recovery automation: AES-256-GCM encrypted backups, PITR, cross-region S3 replication,
automated restore verification, and RTO/RPO drill telemetry (PROD-010).

Invariants enforced:
- Consistent online snapshots (SQLite ``Connection.backup``; zero partial transactions).
- Authenticated encryption at rest via ``AES-256-GCM`` (256-bit key + 96-bit nonce).
- SHA-256 integrity checksums verified prior to restore.
- Cross-region S3 replication (``us-east-1`` -> ``us-west-2``) with ``AES256`` SSE and
  optional S3 Object Lock (``COMPLIANCE``) for ransomware immutability.
- Measured RTO (< 3600s / 1 hour) and RPO (< 900s / 15 minutes) DR drill verification.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from product_value_system.compliance.audit_logger import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
)

_NONCE_BYTES = 12
_KEY_BYTES = 32
_RTO_TARGET_SECONDS = 3600.0  # < 1 hour
_RPO_TARGET_SECONDS = 900.0  # < 15 minutes


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def generate_backup_key() -> bytes:
    """Generate a cryptographically secure 256-bit AES-GCM backup encryption key."""
    return secrets.token_bytes(_KEY_BYTES)


def encrypt_backup_bytes(plaintext: bytes, key: bytes, *, aad: bytes = b"pvs-dr-v1") -> bytes:
    """Compress with gzip and encrypt with AES-256-GCM (nonce || ciphertext+tag)."""
    if len(key) != _KEY_BYTES:
        raise ValueError(f"AES-256-GCM key must be exactly {_KEY_BYTES} bytes")
    compressed = gzip.compress(plaintext, compresslevel=6)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, compressed, aad)
    return nonce + ciphertext


def decrypt_backup_bytes(blob: bytes, key: bytes, *, aad: bytes = b"pvs-dr-v1") -> bytes:
    """Authenticate, decrypt AES-256-GCM payload, and decompress gzip stream."""
    if len(key) != _KEY_BYTES:
        raise ValueError(f"AES-256-GCM key must be exactly {_KEY_BYTES} bytes")
    if len(blob) <= _NONCE_BYTES + 16:
        raise ValueError("Encrypted backup payload is truncated or malformed")
    nonce = blob[:_NONCE_BYTES]
    ciphertext = blob[_NONCE_BYTES:]
    try:
        compressed = AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise ValueError("Backup integrity check failed: AES-256-GCM tag mismatch") from exc
    return gzip.decompress(compressed)


@dataclass(frozen=True)
class BackupManifest:
    """Immutable metadata describing a single encrypted backup snapshot."""

    backup_id: str
    backup_type: str  # full | diff | pitr_wal
    created_at: datetime
    source_path: str
    artifact_path: str
    raw_size_bytes: int
    encrypted_size_bytes: int
    plaintext_sha256: str
    ciphertext_sha256: str
    encryption: str = "AES-256-GCM"
    replicated_s3_key: str | None = None
    replicated_region: str | None = None
    object_lock_mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "backup_type": self.backup_type,
            "created_at": _ensure_utc(self.created_at).isoformat(),
            "source_path": self.source_path,
            "artifact_path": self.artifact_path,
            "raw_size_bytes": self.raw_size_bytes,
            "encrypted_size_bytes": self.encrypted_size_bytes,
            "plaintext_sha256": self.plaintext_sha256,
            "ciphertext_sha256": self.ciphertext_sha256,
            "encryption": self.encryption,
            "replicated_s3_key": self.replicated_s3_key,
            "replicated_region": self.replicated_region,
            "object_lock_mode": self.object_lock_mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackupManifest:
        return cls(
            backup_id=str(data["backup_id"]),
            backup_type=str(data["backup_type"]),
            created_at=_ensure_utc(datetime.fromisoformat(str(data["created_at"]))),
            source_path=str(data["source_path"]),
            artifact_path=str(data["artifact_path"]),
            raw_size_bytes=int(data["raw_size_bytes"]),
            encrypted_size_bytes=int(data["encrypted_size_bytes"]),
            plaintext_sha256=str(data["plaintext_sha256"]),
            ciphertext_sha256=str(data["ciphertext_sha256"]),
            encryption=str(data.get("encryption", "AES-256-GCM")),
            replicated_s3_key=data.get("replicated_s3_key"),
            replicated_region=data.get("replicated_region"),
            object_lock_mode=data.get("object_lock_mode"),
        )


@dataclass
class DRDrillReport:
    """Empirical Disaster Recovery drill result measuring RTO, RPO, and data integrity."""

    drill_id: str
    executed_at: datetime
    backup_id: str
    rto_seconds: float
    rpo_seconds: float
    rto_met: bool
    rpo_met: bool
    integrity_verified: bool
    table_row_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.rto_met and self.rpo_met and self.integrity_verified and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "drill_id": self.drill_id,
            "executed_at": _ensure_utc(self.executed_at).isoformat(),
            "backup_id": self.backup_id,
            "rto_seconds": round(self.rto_seconds, 4),
            "rpo_seconds": round(self.rpo_seconds, 4),
            "rto_met": self.rto_met,
            "rpo_met": self.rpo_met,
            "integrity_verified": self.integrity_verified,
            "passed": self.passed,
            "table_row_counts": dict(self.table_row_counts),
            "errors": list(self.errors),
        }


class DisasterRecoveryManager:
    """Manages encrypted backups, PITR snapshots, cross-region S3 replication, and DR drills."""

    def __init__(
        self,
        backup_dir: Path | str,
        encryption_key: bytes,
        *,
        s3_bucket: str = "pvs-backups",
        replica_region: str = "us-west-2",
        s3_client: Any | None = None,
        enable_object_lock: bool = True,
        object_lock_days: int = 30,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        if len(encryption_key) != _KEY_BYTES:
            raise ValueError(f"encryption_key must be {_KEY_BYTES} bytes (AES-256)")
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._key = encryption_key
        self.s3_bucket = s3_bucket
        self.replica_region = replica_region
        self._s3_client = s3_client
        self.enable_object_lock = enable_object_lock
        self.object_lock_days = max(1, object_lock_days)
        self.audit_logger = audit_logger
        self._catalog_path = self.backup_dir / "catalog.json"

    def _load_catalog(self) -> list[BackupManifest]:
        if not self._catalog_path.is_file():
            return []
        raw = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        return [BackupManifest.from_dict(item) for item in raw]

    def _save_catalog(self, items: list[BackupManifest]) -> None:
        self._catalog_path.write_text(
            json.dumps([m.to_dict() for m in items], indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def list_backups(self) -> list[BackupManifest]:
        """Return all recorded backups ordered oldest to newest."""
        items = self._load_catalog()
        items.sort(key=lambda m: m.created_at)
        return items

    async def create_backup(
        self,
        source_db: Path | str,
        *,
        backup_type: str = "full",
        timestamp: datetime | None = None,
        replicate_to_s3: bool = False,
    ) -> BackupManifest:
        """Create a consistent, AES-256-GCM encrypted online backup of ``source_db``."""
        src = Path(source_db)
        if not src.is_file():
            raise FileNotFoundError(f"Source database not found: {src}")

        created_at = _ensure_utc(timestamp) if timestamp else datetime.now(UTC)
        backup_id = f"{backup_type}-{created_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"

        # Perform consistent online SQLite backup into an isolated temp file (zero downtime)
        with tempfile.TemporaryDirectory() as tmp_dir:
            consistent_copy = Path(tmp_dir) / "snapshot.db"
            src_conn = sqlite3.connect(str(src), timeout=30.0)
            dst_conn = sqlite3.connect(str(consistent_copy))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()
            raw_bytes = consistent_copy.read_bytes()

        encrypted = encrypt_backup_bytes(raw_bytes, self._key)
        artifact_path = self.backup_dir / f"{backup_id}.db.gz.enc"
        artifact_path.write_bytes(encrypted)

        replicated_key: str | None = None
        replicated_region: str | None = None
        lock_mode: str | None = None

        if replicate_to_s3:
            replicated_key = (
                f"backups/{created_at.year}/{created_at.month:02d}/{artifact_path.name}"
            )
            replicated_region = self.replica_region
            client = self._get_s3_client()
            put_kwargs: dict[str, Any] = {
                "Bucket": self.s3_bucket,
                "Key": replicated_key,
                "Body": encrypted,
                "ServerSideEncryption": "AES256",
                "Metadata": {
                    "backup-id": backup_id,
                    "backup-type": backup_type,
                    "replica-region": self.replica_region,
                    "ciphertext-sha256": hashlib.sha256(encrypted).hexdigest(),
                },
            }
            if self.enable_object_lock:
                lock_mode = "COMPLIANCE"
                put_kwargs["ObjectLockMode"] = lock_mode
                put_kwargs["ObjectLockRetainUntilDate"] = created_at + timedelta(
                    days=self.object_lock_days
                )
            client.put_object(**put_kwargs)

        manifest = BackupManifest(
            backup_id=backup_id,
            backup_type=backup_type,
            created_at=created_at,
            source_path=str(src),
            artifact_path=str(artifact_path),
            raw_size_bytes=len(raw_bytes),
            encrypted_size_bytes=len(encrypted),
            plaintext_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            ciphertext_sha256=hashlib.sha256(encrypted).hexdigest(),
            replicated_s3_key=replicated_key,
            replicated_region=replicated_region,
            object_lock_mode=lock_mode,
        )

        catalog = self._load_catalog()
        catalog.append(manifest)
        self._save_catalog(catalog)

        if self.audit_logger is not None:
            await self.audit_logger.log(
                AuditEvent(
                    event_type=AuditEventType.DATA_EXPORT,
                    timestamp=datetime.now(UTC),
                    user_id=None,
                    subject_id=None,
                    action=f"Encrypted {backup_type} database backup created ({backup_id})",
                    resource_type="database_backup",
                    resource_id=backup_id,
                    result="success",
                    metadata={
                        "raw_size_bytes": manifest.raw_size_bytes,
                        "encrypted_size_bytes": manifest.encrypted_size_bytes,
                        "replicated_region": replicated_region,
                        "object_lock_mode": lock_mode,
                    },
                )
            )

        return manifest

    def select_pitr_backup(self, target_time: datetime) -> BackupManifest:
        """Select the latest backup snapshot at or before ``target_time`` for PITR."""
        target_utc = _ensure_utc(target_time)
        candidates = [m for m in self.list_backups() if m.created_at <= target_utc]
        if not candidates:
            raise ValueError(
                f"No recoverable backup exists at or before {target_utc.isoformat()}"
            )
        return candidates[-1]

    async def restore_backup(
        self,
        destination_db: Path | str,
        *,
        backup_id: str | None = None,
        pitr_target: datetime | None = None,
    ) -> BackupManifest:
        """Verify checksums, decrypt AES-256-GCM snapshot, and restore atomically."""
        backups = self.list_backups()
        if not backups:
            raise ValueError("No backups available in catalog")

        if pitr_target is not None:
            manifest = self.select_pitr_backup(pitr_target)
        elif backup_id is not None:
            matched = [m for m in backups if m.backup_id == backup_id]
            if not matched:
                raise KeyError(f"Backup ID not found: {backup_id}")
            manifest = matched[0]
        else:
            manifest = backups[-1]

        artifact = Path(manifest.artifact_path)
        encrypted = artifact.read_bytes()
        actual_ct_hash = hashlib.sha256(encrypted).hexdigest()
        if actual_ct_hash != manifest.ciphertext_sha256:
            raise ValueError(
                f"Backup ciphertext SHA-256 mismatch for {manifest.backup_id} (tamper/corruption)"
            )

        plaintext = decrypt_backup_bytes(encrypted, self._key)
        actual_pt_hash = hashlib.sha256(plaintext).hexdigest()
        if actual_pt_hash != manifest.plaintext_sha256:
            raise ValueError(f"Restored plaintext SHA-256 mismatch for {manifest.backup_id}")

        dest = Path(destination_db)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_dest = dest.with_suffix(dest.suffix + ".restoring")
        tmp_dest.write_bytes(plaintext)
        shutil.move(str(tmp_dest), str(dest))

        if self.audit_logger is not None:
            await self.audit_logger.log(
                AuditEvent(
                    event_type=AuditEventType.DATA_WRITE,
                    timestamp=datetime.now(UTC),
                    user_id=None,
                    subject_id=None,
                    action=f"Database restored from backup {manifest.backup_id}",
                    resource_type="database_restore",
                    resource_id=manifest.backup_id,
                    result="success",
                    metadata={
                        "destination": str(dest),
                        "pitr_target": _ensure_utc(pitr_target).isoformat()
                        if pitr_target
                        else None,
                    },
                )
            )

        return manifest

    async def verify_backup(
        self,
        *,
        backup_id: str | None = None,
        required_tables: tuple[str, ...] = (),
        min_rows_by_table: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Restore a backup into an isolated temp DB and run SQLite PRAGMA integrity_check."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_db = Path(tmp_dir) / "verify_restore.db"
            manifest = await self.restore_backup(test_db, backup_id=backup_id)

            conn = sqlite3.connect(str(test_db))
            try:
                integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
                integrity_ok = bool(integrity_row and integrity_row[0] == "ok")
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }
                counts: dict[str, int] = {}
                for tbl in sorted(tables):
                    # Safe: tbl comes strictly from sqlite_master table names
                    row = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()
                    counts[tbl] = int(row[0]) if row else 0
            finally:
                conn.close()

        missing_tables = [t for t in required_tables if t not in counts]
        under_min = [
            t
            for t, minimum in (min_rows_by_table or {}).items()
            if counts.get(t, 0) < minimum
        ]
        passed = integrity_ok and not missing_tables and not under_min
        return {
            "backup_id": manifest.backup_id,
            "integrity_ok": integrity_ok,
            "table_row_counts": counts,
            "missing_tables": missing_tables,
            "under_min_tables": under_min,
            "passed": passed,
        }

    async def execute_dr_drill(
        self,
        *,
        backup_id: str | None = None,
        required_tables: tuple[str, ...] = (),
        min_rows_by_table: dict[str, int] | None = None,
        now: datetime | None = None,
        rto_limit_seconds: float = _RTO_TARGET_SECONDS,
        rpo_limit_seconds: float = _RPO_TARGET_SECONDS,
    ) -> DRDrillReport:
        """Run an automated DR drill measuring actual RTO, RPO, and restore integrity."""
        ref_now = _ensure_utc(now) if now else datetime.now(UTC)
        drill_id = f"dr-drill-{ref_now.strftime('%Y%m%dT%H%M%SZ')}"
        errors: list[str] = []

        t0 = time.perf_counter()
        verification = await self.verify_backup(
            backup_id=backup_id,
            required_tables=required_tables,
            min_rows_by_table=min_rows_by_table,
        )
        rto_seconds = time.perf_counter() - t0

        backups = {m.backup_id: m for m in self.list_backups()}
        manifest = backups[verification["backup_id"]]
        rpo_seconds = max(0.0, (ref_now - manifest.created_at).total_seconds())

        if verification["missing_tables"]:
            errors.append(f"Missing tables: {verification['missing_tables']}")
        if verification["under_min_tables"]:
            errors.append(f"Tables below minimum rows: {verification['under_min_tables']}")

        report = DRDrillReport(
            drill_id=drill_id,
            executed_at=ref_now,
            backup_id=manifest.backup_id,
            rto_seconds=rto_seconds,
            rpo_seconds=rpo_seconds,
            rto_met=rto_seconds < rto_limit_seconds,
            rpo_met=rpo_seconds < rpo_limit_seconds,
            integrity_verified=bool(verification["passed"]),
            table_row_counts=verification["table_row_counts"],
            errors=errors,
        )

        if self.audit_logger is not None:
            await self.audit_logger.log(
                AuditEvent(
                    event_type=AuditEventType.CONFIG_CHANGED,
                    timestamp=datetime.now(UTC),
                    user_id=None,
                    subject_id=None,
                    action=f"Disaster Recovery drill {drill_id} completed",
                    resource_type="dr_drill",
                    resource_id=drill_id,
                    result="success" if report.passed else "failure",
                    metadata=report.to_dict(),
                )
            )

        return report

    def _get_s3_client(self) -> Any:
        if self._s3_client is None:
            import boto3

            self._s3_client = boto3.client("s3", region_name=self.replica_region)
        return self._s3_client


__all__ = [
    "BackupManifest",
    "DRDrillReport",
    "DisasterRecoveryManager",
    "decrypt_backup_bytes",
    "encrypt_backup_bytes",
    "generate_backup_key",
]
