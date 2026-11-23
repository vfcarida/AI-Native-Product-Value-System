"""
GDPR Data Subject Rights (DSAR) automation — Articles 15 & 17 (PROD-006).

Implements:
- Article 15 (Right of Access): ``GDPRHandler.export_user_data()`` collecting profile,
  agent executions, redacted model API calls, and audit trail into a portable ZIP archive.
- Article 17 (Right to Erasure): ``GDPRHandler.delete_user_data()`` anonymizing user
  profiles and deleting operational execution/model data while retaining immutable audit
  logs by default (Article 17(3)(b) legal obligation) or anonymizing subject PII on request.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from product_value_system.compliance.audit_logger import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
    redact_sensitive_metadata,
)

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

_SQLITE_GDPR_DDL = """
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY,
    email TEXT,
    full_name TEXT,
    role TEXT NOT NULL DEFAULT 'user',
    metadata TEXT,
    created_at TEXT NOT NULL,
    anonymized_at TEXT
);

CREATE TABLE IF NOT EXISTS user_agent_executions (
    execution_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_gdpr_exec_user ON user_agent_executions(user_id);

CREATE TABLE IF NOT EXISTS user_model_calls (
    call_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gdpr_calls_user ON user_model_calls(user_id);
"""


def _sanitize_filename_segment(value: str) -> str:
    """Strip unsafe path characters from a user ID used in export archive filenames."""
    cleaned = _SAFE_FILENAME_RE.sub("_", value.strip()).strip("._")
    return cleaned[:64] or "subject"


def _redact_model_call(call: Mapping[str, Any]) -> dict[str, Any]:
    """Return a privacy-safe representation of a model API call for GDPR exports."""
    redacted = dict(call)
    if "prompt" in redacted and redacted["prompt"] is not None:
        raw_prompt = str(redacted["prompt"])
        redacted["prompt"] = f"[REDACTED_PROMPT length={len(raw_prompt)}]"
    if "response" in redacted and redacted["response"] is not None:
        raw_resp = str(redacted["response"])
        redacted["response"] = f"[REDACTED_RESPONSE length={len(raw_resp)}]"
    return dict(redact_sensitive_metadata(redacted))


@runtime_checkable
class GDPRDataStore(Protocol):
    """Persistence abstraction for user profile and operational records subject to GDPR."""

    async def get_user_profile(self, user_id: str) -> dict[str, Any] | None: ...

    async def get_agent_executions(self, user_id: str) -> list[dict[str, Any]]: ...

    async def get_model_calls(self, user_id: str) -> list[dict[str, Any]]: ...

    async def anonymize_user_profile(self, user_id: str) -> int: ...

    async def delete_agent_executions(self, user_id: str) -> int: ...

    async def delete_model_calls(self, user_id: str) -> int: ...


class InMemoryGDPRDataStore:
    """In-memory ``GDPRDataStore`` for deterministic testing and lightweight setups."""

    def __init__(self) -> None:
        self.profiles: dict[str, dict[str, Any]] = {}
        self.agent_executions: dict[str, list[dict[str, Any]]] = {}
        self.model_calls: dict[str, list[dict[str, Any]]] = {}

    def seed_user(
        self,
        user_id: str,
        *,
        profile: dict[str, Any] | None = None,
        agent_executions: list[dict[str, Any]] | None = None,
        model_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        if profile is not None:
            self.profiles[user_id] = dict(profile)
        if agent_executions is not None:
            self.agent_executions[user_id] = [dict(x) for x in agent_executions]
        if model_calls is not None:
            self.model_calls[user_id] = [dict(x) for x in model_calls]

    async def get_user_profile(self, user_id: str) -> dict[str, Any] | None:
        prof = self.profiles.get(user_id)
        return dict(prof) if prof is not None else None

    async def get_agent_executions(self, user_id: str) -> list[dict[str, Any]]:
        return [dict(x) for x in self.agent_executions.get(user_id, [])]

    async def get_model_calls(self, user_id: str) -> list[dict[str, Any]]:
        return [dict(x) for x in self.model_calls.get(user_id, [])]

    async def anonymize_user_profile(self, user_id: str) -> int:
        prof = self.profiles.get(user_id)
        if prof is None:
            return 0
        prof["email"] = None
        prof["full_name"] = None
        prof["metadata"] = {}
        prof["anonymized_at"] = datetime.now(UTC).isoformat()
        return 1

    async def delete_agent_executions(self, user_id: str) -> int:
        items = self.agent_executions.pop(user_id, [])
        return len(items)

    async def delete_model_calls(self, user_id: str) -> int:
        items = self.model_calls.pop(user_id, [])
        return len(items)


class SQLiteGDPRDataStore:
    """SQLite-backed ``GDPRDataStore`` supporting real profile anonymization and deletion."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SQLITE_GDPR_DDL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_user_profile(
        self,
        user_id: str,
        *,
        email: str | None,
        full_name: str | None,
        role: str = "user",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO user_profiles (user_id, email, full_name, role, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                email = excluded.email,
                full_name = excluded.full_name,
                role = excluded.role,
                metadata = excluded.metadata
            """,
            (
                user_id,
                email,
                full_name,
                role,
                json.dumps(metadata or {}, sort_keys=True),
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()

    def record_agent_execution(
        self,
        execution_id: str,
        user_id: str,
        agent_name: str,
        *,
        status: str = "completed",
        cost_usd: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO user_agent_executions (
                execution_id, user_id, agent_name, status, cost_usd, created_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                execution_id,
                user_id,
                agent_name,
                status,
                cost_usd,
                datetime.now(UTC).isoformat(),
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        self._conn.commit()

    def record_model_call(
        self,
        call_id: str,
        user_id: str,
        provider: str,
        model: str,
        *,
        prompt: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO user_model_calls (
                call_id, user_id, provider, model, prompt,
                input_tokens, output_tokens, cost_usd, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                user_id,
                provider,
                model,
                prompt,
                input_tokens,
                output_tokens,
                cost_usd,
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()

    async def get_user_profile(self, user_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["metadata"] = json.loads(data["metadata"]) if data.get("metadata") else {}
        return data

    async def get_agent_executions(self, user_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM user_agent_executions WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item["metadata"]) if item.get("metadata") else {}
            results.append(item)
        return results

    async def get_model_calls(self, user_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM user_model_calls WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    async def anonymize_user_profile(self, user_id: str) -> int:
        cursor = self._conn.execute(
            """
            UPDATE user_profiles
            SET email = NULL,
                full_name = NULL,
                metadata = '{}',
                anonymized_at = ?
            WHERE user_id = ?
            """,
            (datetime.now(UTC).isoformat(), user_id),
        )
        self._conn.commit()
        return cursor.rowcount

    async def delete_agent_executions(self, user_id: str) -> int:
        cursor = self._conn.execute(
            "DELETE FROM user_agent_executions WHERE user_id = ?", (user_id,)
        )
        self._conn.commit()
        return cursor.rowcount

    async def delete_model_calls(self, user_id: str) -> int:
        cursor = self._conn.execute(
            "DELETE FROM user_model_calls WHERE user_id = ?", (user_id,)
        )
        self._conn.commit()
        return cursor.rowcount


class GDPRHandler:
    """Automated handler for GDPR Data Subject Access (Art. 15) and Erasure (Art. 17)."""

    def __init__(
        self,
        audit_logger: AuditLogger,
        data_store: GDPRDataStore | None = None,
        *,
        dpo_email: str = "privacy@example.com",
        support_email: str = "support@example.com",
        privacy_policy_url: str = "https://example.com/privacy",
        dsar_sla_days: int = 30,
    ) -> None:
        self.audit_logger = audit_logger
        self.data_store: GDPRDataStore = data_store or InMemoryGDPRDataStore()
        self.dpo_email = dpo_email
        self.support_email = support_email
        self.privacy_policy_url = privacy_policy_url
        self.dsar_sla_days = dsar_sla_days

    async def export_user_data(
        self,
        user_id: str,
        requester_email: str,
        output_dir: Path | str,
        *,
        export_format: str = "json",
        actor_user_id: str | None = None,
    ) -> Path:
        """Export all personal data for ``user_id`` into a portable ZIP archive (GDPR Art. 15)."""
        started_monotonic = time.monotonic()
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        await self.audit_logger.log(
            AuditEvent(
                event_type=AuditEventType.GDPR_EXPORT_REQUESTED,
                timestamp=datetime.now(UTC),
                user_id=actor_user_id,
                subject_id=user_id,
                action=f"GDPR data export requested for user {user_id}",
                resource_type="user_data",
                resource_id=user_id,
                result="started",
                metadata={"requester_email": requester_email, "export_format": export_format},
            )
        )

        # Ensure any queued events (including the export request) are flushed before snapshot
        await self.audit_logger.flush()

        profile = await self._export_user_profile(user_id)
        agent_executions = await self._export_agent_executions(user_id)
        model_calls = await self._export_model_calls(user_id)
        audit_logs = await self._export_audit_logs(user_id)

        user_data: dict[str, Any] = {
            "profile": profile,
            "agent_executions": agent_executions,
            "model_calls": model_calls,
            "audit_logs": audit_logs,
            "metadata": {
                "export_timestamp": datetime.now(UTC).isoformat(),
                "user_id": user_id,
                "format_version": "1.0",
                "gdpr_article": "Article 15 - Right of Access",
                "dsar_sla_days": self.dsar_sla_days,
            },
        }

        safe_uid = _sanitize_filename_segment(user_id)
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        zip_path = (out_dir / f"gdpr_export_{safe_uid}_{stamp}.zip").resolve()
        if out_dir.resolve() not in zip_path.parents:
            raise ValueError("Resolved export path escapes output directory")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            zipf.writestr("user_data.json", json.dumps(user_data, indent=2, sort_keys=True))
            zipf.writestr("README.txt", self._generate_export_readme())
            if export_format.lower() == "csv":
                csv_content = await self.audit_logger.export_csv(
                    subject_id=user_id,
                    actor_user_id=actor_user_id,
                )
                zipf.writestr("audit_logs.csv", csv_content)

        elapsed_seconds = round(time.monotonic() - started_monotonic, 4)

        await self.audit_logger.log(
            AuditEvent(
                event_type=AuditEventType.DATA_EXPORT,
                timestamp=datetime.now(UTC),
                user_id=actor_user_id,
                subject_id=user_id,
                action=f"GDPR data export completed for user {user_id}",
                resource_type="user_data",
                resource_id=user_id,
                result="success",
                metadata={
                    "export_path": str(zip_path),
                    "size_bytes": zip_path.stat().st_size,
                    "elapsed_seconds": elapsed_seconds,
                },
            )
        )

        return zip_path

    async def delete_user_data(
        self,
        user_id: str,
        reason: str,
        retain_audit_logs: bool = True,
        *,
        actor_user_id: str | None = None,
    ) -> dict[str, int]:
        """Anonymize profile and erase operational records for ``user_id`` (GDPR Art. 17).

        Audit logs are retained by default for 7 years under GDPR Article 17(3)(b)
        legal compliance obligations; pass ``retain_audit_logs=False`` to anonymize
        subject identifiers within audit logs.
        """
        await self.audit_logger.log(
            AuditEvent(
                event_type=AuditEventType.GDPR_DELETION_REQUESTED,
                timestamp=datetime.now(UTC),
                user_id=actor_user_id,
                subject_id=user_id,
                action=f"GDPR data deletion requested for user {user_id}",
                resource_type="user_data",
                resource_id=user_id,
                result="started",
                metadata={"reason": reason, "retain_audit_logs": retain_audit_logs},
            )
        )
        await self.audit_logger.flush()

        deletion_counts: dict[str, int] = {
            "profile": await self.data_store.anonymize_user_profile(user_id),
            "agent_executions": await self.data_store.delete_agent_executions(user_id),
            "model_calls": await self.data_store.delete_model_calls(user_id),
            "audit_logs": (
                0
                if retain_audit_logs
                else await self.audit_logger.store.anonymize_subject(user_id)
            ),
        }

        target_label = user_id if retain_audit_logs else "[ANONYMIZED]"
        await self.audit_logger.log(
            AuditEvent(
                event_type=AuditEventType.DATA_DELETE,
                timestamp=datetime.now(UTC),
                user_id=actor_user_id,
                subject_id=target_label,
                action=f"GDPR data deletion completed for user {target_label}",
                resource_type="user_data",
                resource_id=target_label,
                result="success",
                metadata={
                    "deletion_counts": deletion_counts,
                    "reason": reason,
                    "retain_audit_logs": retain_audit_logs,
                },
            )
        )

        return deletion_counts

    async def _export_user_profile(self, user_id: str) -> dict[str, Any]:
        profile = await self.data_store.get_user_profile(user_id)
        if profile is None:
            return {"user_id": user_id, "status": "not_found"}
        return dict(redact_sensitive_metadata(profile))

    async def _export_agent_executions(self, user_id: str) -> list[dict[str, Any]]:
        executions = await self.data_store.get_agent_executions(user_id)
        return [dict(redact_sensitive_metadata(item)) for item in executions]

    async def _export_model_calls(self, user_id: str) -> list[dict[str, Any]]:
        calls = await self.data_store.get_model_calls(user_id)
        return [_redact_model_call(call) for call in calls]

    async def _export_audit_logs(self, user_id: str) -> list[dict[str, Any]]:
        as_subject = await self.audit_logger.search(subject_id=user_id, limit=1000)
        as_actor = await self.audit_logger.search(user_id=user_id, limit=1000)
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for ev in [*as_subject, *as_actor]:
            key = ev.event_id or f"{ev.timestamp.isoformat()}:{ev.action}"
            if key not in seen:
                seen.add(key)
                merged.append(ev.to_dict())
        merged.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
        return merged

    def _generate_export_readme(self) -> str:
        return (
            "# GDPR Data Export\n\n"
            "This archive contains your personal data as processed by the "
            "AI-Native Product Value System.\n\n"
            "## Contents\n"
            "- user_data.json: All your data in machine-readable JSON format\n\n"
            "## Data Categories\n"
            "- Profile: User account information\n"
            "- Agent Executions: History of agent runs you initiated\n"
            "- Model Calls: API calls made on your behalf (with redacted content)\n"
            "- Audit Logs: Security and compliance events related to your account\n\n"
            "## Your Rights\n"
            "Under GDPR, you have the right to:\n"
            f"- Rectify inaccurate data: Contact {self.support_email}\n"
            "- Delete your data: Submit an Article 17 erasure request\n"
            "- Object to processing: Contact our Data Protection Officer\n\n"
            "## Questions?\n"
            f"Email: {self.dpo_email}\n"
            f"Privacy Policy: {self.privacy_policy_url}\n"
        )


__all__ = [
    "GDPRDataStore",
    "GDPRHandler",
    "InMemoryGDPRDataStore",
    "SQLiteGDPRDataStore",
]
