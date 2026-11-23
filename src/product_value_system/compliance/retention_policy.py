"""
Automated audit retention, cold S3 Glacier archival, and quarterly access reviews (PROD-006).

Lifecycle policy:
- Hot storage (PostgreSQL / SQLite): 90 days (fast audit search & incident response)
- Cold storage (S3 Glacier + AES-256 + gzip level 9): 2,555 days (7 years)
- Automatic expiration deletion: removes cold S3 objects older than 7 years
- SOC2 CC6.2 / CC6.3 quarterly access review automation
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from product_value_system.compliance.audit_logger import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
    AuditStore,
    SQLiteAuditStore,
)

logger = logging.getLogger(__name__)

_PRIVILEGED_ROLES: frozenset[str] = frozenset(
    {"admin", "security_admin", "compliance_officer", "superadmin", "owner"}
)
_PRIVILEGED_PERMISSIONS: frozenset[str] = frozenset(
    {"audit:read", "compliance:admin", "secrets:write", "policy:write", "users:admin"}
)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class RetentionPolicy:
    """Enforce hot-to-cold S3 archival (90d) and post-retention deletion (7y)."""

    def __init__(
        self,
        audit_store: AuditStore,
        s3_bucket: str,
        *,
        hot_storage_days: int = 90,
        cold_storage_days: int = 2555,
        region_name: str = "us-east-1",
        storage_class: str = "GLACIER",
        server_side_encryption: str = "AES256",
        batch_size: int = 1000,
        s3_client: Any | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        if hot_storage_days < 1:
            raise ValueError("hot_storage_days must be >= 1")
        if cold_storage_days < hot_storage_days:
            raise ValueError("cold_storage_days must be >= hot_storage_days")
        self.audit_store = audit_store
        self.s3_bucket = s3_bucket
        self.hot_storage_days = hot_storage_days
        self.cold_storage_days = cold_storage_days
        self.region_name = region_name
        self.storage_class = storage_class
        self.server_side_encryption = server_side_encryption
        self.batch_size = max(1, batch_size)
        self._s3_client = s3_client
        self.audit_logger = audit_logger

    @property
    def s3_client(self) -> Any:
        """Lazily initialize boto3 S3 client so offline instantiation never hits AWS IMDS."""
        if self._s3_client is None:
            import boto3

            self._s3_client = boto3.client("s3", region_name=self.region_name)
        return self._s3_client

    async def archive_old_logs(
        self,
        *,
        now: datetime | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Compress and archive audit logs older than ``hot_storage_days`` to S3 Glacier."""
        reference_now = _ensure_utc(now) if now else datetime.now(UTC)
        cutoff_date = reference_now - timedelta(days=self.hot_storage_days)

        stats: dict[str, Any] = {
            "logs_archived": 0,
            "uncompressed_bytes": 0,
            "bytes_transferred": 0,
            "compression_ratio_pct": 0.0,
            "objects_uploaded": 0,
            "uploaded_keys": [],
            "errors": 0,
            "dry_run": dry_run,
            "cutoff_date": cutoff_date.isoformat(),
        }

        old_logs = await self.audit_store.query_older_than(cutoff_date, limit=100000)
        if not old_logs:
            return stats

        run_stamp = reference_now.strftime("%Y%m%dT%H%M%SZ")
        for batch_index, offset in enumerate(range(0, len(old_logs), self.batch_size)):
            batch = old_logs[offset : offset + self.batch_size]
            try:
                batch_data = [self._serialize_log(ev) for ev in batch]
                raw_bytes = json.dumps(batch_data, indent=2, sort_keys=True).encode("utf-8")
                compressed_data = gzip.compress(raw_bytes, compresslevel=9)

                first_ts = _ensure_utc(batch[0].timestamp)
                s3_key = (
                    f"audit_logs/year={first_ts.year}/month={first_ts.month:02d}/"
                    f"batch_{run_stamp}_{batch_index:04d}.json.gz"
                )

                if not dry_run:
                    self.s3_client.put_object(
                        Bucket=self.s3_bucket,
                        Key=s3_key,
                        Body=compressed_data,
                        StorageClass=self.storage_class,
                        ServerSideEncryption=self.server_side_encryption,
                        ContentType="application/gzip",
                        Metadata={
                            "record_count": str(len(batch)),
                            "cutoff_date": cutoff_date.isoformat(),
                        },
                    )
                    event_ids = [ev.event_id for ev in batch if ev.event_id]
                    await self.audit_store.archive_and_prune(event_ids)

                stats["logs_archived"] += len(batch)
                stats["uncompressed_bytes"] += len(raw_bytes)
                stats["bytes_transferred"] += len(compressed_data)
                stats["objects_uploaded"] += 1
                stats["uploaded_keys"].append(s3_key)
            except Exception as exc:
                logger.exception("Failed to archive audit batch %d: %s", batch_index, exc)
                stats["errors"] += 1

        if stats["uncompressed_bytes"] > 0:
            saved = stats["uncompressed_bytes"] - stats["bytes_transferred"]
            stats["compression_ratio_pct"] = round(
                (saved / stats["uncompressed_bytes"]) * 100.0, 2
            )

        if self.audit_logger is not None and not dry_run and stats["logs_archived"] > 0:
            await self.audit_logger.log(
                AuditEvent(
                    event_type=AuditEventType.CONFIG_CHANGED,
                    timestamp=datetime.now(UTC),
                    user_id=None,
                    subject_id=None,
                    action=f"Archived {stats['logs_archived']} audit logs to s3://{self.s3_bucket}",
                    resource_type="audit_archive",
                    resource_id=self.s3_bucket,
                    result="success" if stats["errors"] == 0 else "partial",
                    metadata={
                        "logs_archived": stats["logs_archived"],
                        "bytes_transferred": stats["bytes_transferred"],
                        "compression_ratio_pct": stats["compression_ratio_pct"],
                        "errors": stats["errors"],
                    },
                )
            )

        return stats

    async def delete_expired_logs(
        self,
        *,
        now: datetime | None = None,
        dry_run: bool = False,
    ) -> int:
        """Delete cold S3 archive objects older than ``cold_storage_days`` (default 7 years)."""
        reference_now = _ensure_utc(now) if now else datetime.now(UTC)
        cutoff_date = reference_now - timedelta(days=self.cold_storage_days)

        paginator = self.s3_client.get_paginator("list_objects_v2")
        deleted_count = 0

        for page in paginator.paginate(Bucket=self.s3_bucket, Prefix="audit_logs/"):
            for obj in page.get("Contents", []):
                last_modified = _ensure_utc(obj["LastModified"])
                if last_modified < cutoff_date:
                    if not dry_run:
                        self.s3_client.delete_object(Bucket=self.s3_bucket, Key=obj["Key"])
                    deleted_count += 1

        if self.audit_logger is not None and not dry_run and deleted_count > 0:
            await self.audit_logger.log(
                AuditEvent(
                    event_type=AuditEventType.DATA_DELETE,
                    timestamp=datetime.now(UTC),
                    user_id=None,
                    subject_id=None,
                    action=f"Deleted {deleted_count} expired audit archive objects from s3://{self.s3_bucket}",
                    resource_type="audit_archive",
                    resource_id=self.s3_bucket,
                    result="success",
                    metadata={
                        "deleted_objects": deleted_count,
                        "cold_storage_days": self.cold_storage_days,
                        "cutoff_date": cutoff_date.isoformat(),
                    },
                )
            )

        return deleted_count

    @staticmethod
    def _serialize_log(event: AuditEvent) -> dict[str, Any]:
        """Serialize an ``AuditEvent`` into an archival JSON dictionary."""
        return event.to_dict()


# ---------------------------------------------------------------------------
# SOC2 CC6.2 / CC6.3 Quarterly Access Review Automation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccessRecord:
    """User or service-account entitlement snapshot for quarterly access review."""

    user_id: str
    role: str
    permissions: tuple[str, ...] = ()
    last_active_at: datetime | None = None
    mfa_enabled: bool = True
    granted_at: datetime | None = None
    is_service_account: bool = False


@dataclass(frozen=True)
class AccessReviewFinding:
    """Compliance finding raised during a SOC2 CC6.2/CC6.3 quarterly access review."""

    user_id: str
    severity: str  # high, medium, low
    category: str
    recommendation: str


@dataclass
class AccessReviewReport:
    """Auditable quarterly access review artifact."""

    review_quarter: str
    generated_at: datetime
    total_accounts_reviewed: int
    privileged_accounts: int
    findings: list[AccessReviewFinding] = field(default_factory=list)
    approved_accounts: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity == "high" for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_quarter": self.review_quarter,
            "generated_at": _ensure_utc(self.generated_at).isoformat(),
            "total_accounts_reviewed": self.total_accounts_reviewed,
            "privileged_accounts": self.privileged_accounts,
            "finding_count": len(self.findings),
            "passed": self.passed,
            "approved_accounts": list(self.approved_accounts),
            "findings": [
                {
                    "user_id": f.user_id,
                    "severity": f.severity,
                    "category": f.category,
                    "recommendation": f.recommendation,
                }
                for f in self.findings
            ],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# SOC2 Quarterly Access Review — {self.review_quarter}",
            "",
            f"- **Generated At**: {_ensure_utc(self.generated_at).isoformat()}",
            f"- **Accounts Reviewed**: {self.total_accounts_reviewed}",
            f"- **Privileged Accounts**: {self.privileged_accounts}",
            f"- **Findings**: {len(self.findings)}",
            f"- **Status**: {'PASS' if self.passed else 'ACTION_REQUIRED'}",
            "",
        ]
        if self.findings:
            lines.extend(
                [
                    "## Findings Requiring Remediation",
                    "",
                    "| User ID | Severity | Category | Recommendation |",
                    "|---|---|---|---|",
                ]
            )
            for f in self.findings:
                sev = f.severity.upper()
                lines.append(
                    f"| `{f.user_id}` | **{sev}** | `{f.category}` | {f.recommendation} |"
                )
        else:
            lines.append("All reviewed accounts comply with least-privilege and MFA policies.")
        return "\n".join(lines) + "\n"


class QuarterlyAccessReviewer:
    """Automated SOC2 CC6.2 / CC6.3 quarterly access review engine."""

    def __init__(self, *, inactive_days_threshold: int = 90) -> None:
        self.inactive_days_threshold = max(1, inactive_days_threshold)

    async def generate_review_report(
        self,
        accounts: Sequence[AccessRecord],
        *,
        now: datetime | None = None,
        review_quarter: str | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> AccessReviewReport:
        """Inspect account entitlements and cross-reference audit activity."""
        ref_now = _ensure_utc(now) if now else datetime.now(UTC)
        quarter = review_quarter or f"{ref_now.year}-Q{((ref_now.month - 1) // 3) + 1}"
        stale_cutoff = ref_now - timedelta(days=self.inactive_days_threshold)

        findings: list[AccessReviewFinding] = []
        approved: list[str] = []
        privileged_count = 0

        for acc in accounts:
            is_privileged = acc.role.lower() in _PRIVILEGED_ROLES or bool(
                set(acc.permissions) & _PRIVILEGED_PERMISSIONS
            )
            if is_privileged:
                privileged_count += 1

            # Enrich last_active_at from AuditLogger when available
            effective_last_active = _ensure_utc(acc.last_active_at) if acc.last_active_at else None
            if audit_logger is not None:
                recent = await audit_logger.search(user_id=acc.user_id, limit=1)
                if recent and (
                    effective_last_active is None or recent[0].timestamp > effective_last_active
                ):
                    effective_last_active = recent[0].timestamp

            account_findings: list[AccessReviewFinding] = []

            if is_privileged and not acc.is_service_account and not acc.mfa_enabled:
                account_findings.append(
                    AccessReviewFinding(
                        user_id=acc.user_id,
                        severity="high",
                        category="privileged_account_no_mfa",
                        recommendation="Enforce MFA immediately or suspend privileged role.",
                    )
                )

            if effective_last_active is None or effective_last_active < stale_cutoff:
                account_findings.append(
                    AccessReviewFinding(
                        user_id=acc.user_id,
                        severity="high" if is_privileged else "medium",
                        category="dormant_account",
                        recommendation=(
                            f"No activity in >= {self.inactive_days_threshold} days; "
                            "revoke access or require manager recertification."
                        ),
                    )
                )

            if account_findings:
                findings.extend(account_findings)
            else:
                approved.append(acc.user_id)

        report = AccessReviewReport(
            review_quarter=quarter,
            generated_at=ref_now,
            total_accounts_reviewed=len(accounts),
            privileged_accounts=privileged_count,
            findings=findings,
            approved_accounts=approved,
        )

        if audit_logger is not None:
            await audit_logger.log(
                AuditEvent(
                    event_type=AuditEventType.CONFIG_CHANGED,
                    timestamp=ref_now,
                    user_id=None,
                    subject_id=None,
                    action=f"Quarterly access review completed for {quarter}",
                    resource_type="access_review",
                    resource_id=quarter,
                    result="success" if report.passed else "partial",
                    metadata={
                        "total_accounts_reviewed": report.total_accounts_reviewed,
                        "privileged_accounts": report.privileged_accounts,
                        "finding_count": len(report.findings),
                    },
                )
            )

        return report


def _cli_main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for Kubernetes CronJobs (audit-archival-job / audit-deletion-job)."""
    parser = argparse.ArgumentParser(description="PVS Compliance Audit Retention & Archival Job")
    parser.add_argument("--mode", choices=["archive", "delete"], required=True)
    parser.add_argument("--db-path", default=os.getenv("PVS_AUDIT_DB_PATH", "pvs_audit.db"))
    parser.add_argument("--s3-bucket", default=os.getenv("AUDIT_S3_BUCKET", "pvs-audit-archive"))
    parser.add_argument(
        "--hot-days", type=int, default=int(os.getenv("AUDIT_HOT_STORAGE_DAYS", "90"))
    )
    parser.add_argument(
        "--cold-days", type=int, default=int(os.getenv("AUDIT_COLD_STORAGE_DAYS", "2555"))
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    store = SQLiteAuditStore(Path(args.db_path))
    try:
        policy = RetentionPolicy(
            store,
            s3_bucket=args.s3_bucket,
            hot_storage_days=args.hot_days,
            cold_storage_days=args.cold_days,
        )
        if args.mode == "archive":
            stats = asyncio.run(policy.archive_old_logs(dry_run=args.dry_run))
            print(json.dumps(stats, sort_keys=True))
            return 0 if stats.get("errors", 0) == 0 else 1
        deleted = asyncio.run(policy.delete_expired_logs(dry_run=args.dry_run))
        print(json.dumps({"deleted_objects": deleted, "dry_run": args.dry_run}, sort_keys=True))
        return 0
    finally:
        store.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli_main())


__all__ = [
    "AccessRecord",
    "AccessReviewFinding",
    "AccessReviewReport",
    "QuarterlyAccessReviewer",
    "RetentionPolicy",
]
