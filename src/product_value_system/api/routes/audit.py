"""
RBAC-protected Compliance Audit Search & GDPR Data Subject Rights API (PROD-006).

Endpoints:
- ``GET  /api/v1/audit/search``: Search immutable audit logs (``audit:read`` or ``audit:self``)
- ``GET  /api/v1/audit/export``: Export SOC2 auditor evidence in CSV or JSON (``audit:read``)
- ``POST /api/v1/audit/verify``: Verify HMAC-SHA256 signatures across audit logs (``audit:read``)
- ``POST /api/v1/compliance/gdpr/export``: Trigger GDPR Art. 15 export archive
- ``POST /api/v1/compliance/gdpr/delete``: Trigger GDPR Art. 17 erasure / anonymization

Security Controls:
- Constant-time credential lookup via ``hmac.compare_digest``
- Per-principal sliding-window rate limiting (default: 100 requests/hour per SOC2 CC7.2)
- Meta-audit logging (``AUDIT_LOG_ACCESSED``) on every search and export
- Failed authentication and authorization denials recorded as audit events
"""

from __future__ import annotations

import hmac
import tempfile
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from product_value_system.compliance.audit_logger import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
)
from product_value_system.compliance.gdpr_handler import GDPRHandler


@dataclass(frozen=True)
class AuditPrincipal:
    """Authenticated caller identity and RBAC permissions."""

    user_id: str
    role: str
    permissions: frozenset[str]

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


class SlidingWindowRateLimiter:
    """In-memory per-principal sliding-window rate limiter (SOC2 CC7.2)."""

    def __init__(self, max_requests: int = 100, window_seconds: float = 3600.0) -> None:
        self.max_requests = max(1, max_requests)
        self.window_seconds = max(1.0, window_seconds)
        self._hits: dict[str, deque[float]] = {}

    def check_and_record(self, key: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` for ``key``."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        bucket = self._hits.setdefault(key, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            retry_after = max(1, int(self.window_seconds - (now - bucket[0])))
            return False, retry_after
        bucket.append(now)
        return True, 0


class GDPRExportRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    requester_email: str = Field(min_length=3, max_length=254)
    export_format: str = Field(default="json", pattern="^(json|csv)$")


class GDPRDeleteRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)
    retain_audit_logs: bool = True


def _extract_credential(request: Request) -> str:
    provided = request.headers.get("x-api-key") or ""
    if provided:
        return provided.strip()
    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def _resolve_principal(
    request: Request,
    principals_by_key: Mapping[str, AuditPrincipal],
) -> AuditPrincipal | None:
    """Match incoming API key/Bearer token against registered principals in constant time."""
    credential = _extract_credential(request)
    if not credential:
        return None
    matched: AuditPrincipal | None = None
    for api_key, principal in principals_by_key.items():
        if hmac.compare_digest(credential, api_key):
            matched = principal
    return matched


def _parse_iso_datetime(value: str | None, field_name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid ISO-8601 timestamp for '{field_name}'"
        ) from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_event_type(value: str | None) -> AuditEventType | None:
    if not value:
        return None
    try:
        return AuditEventType(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Unknown audit event_type: {value!r}"
        ) from exc


def create_audit_router(
    audit_logger: AuditLogger,
    *,
    principals_by_key: Mapping[str, AuditPrincipal],
    gdpr_handler: GDPRHandler | None = None,
    gdpr_export_dir: Path | None = None,
    rate_limit_per_hour: int = 100,
) -> APIRouter:
    """Build the FastAPI ``APIRouter`` exposing RBAC-protected compliance endpoints."""
    router = APIRouter(prefix="/api/v1", tags=["compliance-audit"])
    limiter = SlidingWindowRateLimiter(max_requests=rate_limit_per_hour, window_seconds=3600.0)

    async def _authenticate_and_limit(request: Request) -> AuditPrincipal:
        principal = _resolve_principal(request, principals_by_key)
        client_ip = request.client.host if request.client else None
        if principal is None:
            await audit_logger.log(
                AuditEvent(
                    event_type=AuditEventType.USER_LOGIN_FAILED,
                    timestamp=datetime.now(UTC),
                    user_id=None,
                    subject_id=None,
                    action=f"Unauthorized compliance API request to {request.url.path}",
                    resource_type="audit_api",
                    resource_id=request.url.path,
                    result="failure",
                    metadata={"method": request.method},
                    client_ip=client_ip,
                    user_agent=request.headers.get("user-agent"),
                )
            )
            raise HTTPException(status_code=401, detail="Authentication required")

        allowed, retry_after = limiter.check_and_record(principal.user_id)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded for compliance API",
                headers={"Retry-After": str(retry_after)},
            )
        return principal

    @router.get("/audit/search")
    async def search_audit_logs(
        request: Request,
        user_id: str | None = Query(default=None),
        subject_id: str | None = Query(default=None),
        event_type: str | None = Query(default=None),
        resource_type: str | None = Query(default=None),
        session_id: str | None = Query(default=None),
        start_time: str | None = Query(default=None),
        end_time: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """Search immutable audit logs with RBAC enforcement and signature verification."""
        principal = await _authenticate_and_limit(request)

        can_read_all = principal.has_permission("audit:read")
        can_read_self = principal.has_permission("audit:self")
        if not can_read_all and not can_read_self:
            raise HTTPException(
                status_code=403,
                detail="Missing required permission: audit:read",
            )

        effective_user_id = user_id
        effective_subject_id = subject_id
        if not can_read_all:
            # Self-only GDPR scope: callers without audit:read may only query their own events.
            if user_id is not None and user_id != principal.user_id:
                raise HTTPException(
                    status_code=403,
                    detail="Cannot query other users' audit logs without audit:read",
                )
            if subject_id is not None and subject_id != principal.user_id:
                raise HTTPException(
                    status_code=403,
                    detail="Cannot query other subjects' audit logs without audit:read",
                )
            effective_subject_id = principal.user_id

        parsed_type = _parse_event_type(event_type)
        parsed_start = _parse_iso_datetime(start_time, "start_time")
        parsed_end = _parse_iso_datetime(end_time, "end_time")

        events = await audit_logger.search(
            user_id=effective_user_id,
            subject_id=effective_subject_id,
            event_type=parsed_type,
            resource_type=resource_type,
            session_id=session_id,
            start_time=parsed_start,
            end_time=parsed_end,
            limit=limit,
            offset=offset,
            actor_user_id=principal.user_id,
            log_access=True,
            verify_integrity=True,
        )
        verification = audit_logger.verify_events(events)

        return {
            "count": len(events),
            "integrity_verified": verification["integrity_verified"],
            "tampered_event_ids": verification["tampered_event_ids"],
            "events": [ev.to_dict() for ev in events],
        }

    @router.get("/audit/export")
    async def export_audit_logs(
        request: Request,
        export_format: str = Query(default="csv", pattern="^(csv|json)$"),
        user_id: str | None = Query(default=None),
        subject_id: str | None = Query(default=None),
        event_type: str | None = Query(default=None),
        start_time: str | None = Query(default=None),
        end_time: str | None = Query(default=None),
        limit: int = Query(default=1000, ge=1, le=1000),
    ) -> Response:
        """Export SOC2 audit evidence in CSV or JSON format (requires ``audit:read``)."""
        principal = await _authenticate_and_limit(request)
        if not principal.has_permission("audit:read"):
            raise HTTPException(status_code=403, detail="Missing required permission: audit:read")

        parsed_type = _parse_event_type(event_type)
        parsed_start = _parse_iso_datetime(start_time, "start_time")
        parsed_end = _parse_iso_datetime(end_time, "end_time")

        if export_format == "csv":
            csv_body = await audit_logger.export_csv(
                user_id=user_id,
                subject_id=subject_id,
                event_type=parsed_type,
                start_time=parsed_start,
                end_time=parsed_end,
                limit=limit,
                actor_user_id=principal.user_id,
            )
            return Response(
                content=csv_body,
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="audit_evidence.csv"'},
            )

        events = await audit_logger.search(
            user_id=user_id,
            subject_id=subject_id,
            event_type=parsed_type,
            start_time=parsed_start,
            end_time=parsed_end,
            limit=limit,
            actor_user_id=principal.user_id,
            log_access=True,
        )
        import json as _json

        return Response(
            content=_json.dumps({"events": [ev.to_dict() for ev in events]}, indent=2),
            media_type="application/json",
        )

    @router.post("/audit/verify")
    async def verify_audit_integrity(
        request: Request,
        limit: int = Query(default=500, ge=1, le=1000),
    ) -> dict[str, Any]:
        """Run an HMAC-SHA256 integrity sweep across stored audit logs (requires ``audit:read``)."""
        principal = await _authenticate_and_limit(request)
        if not principal.has_permission("audit:read"):
            raise HTTPException(status_code=403, detail="Missing required permission: audit:read")

        events = await audit_logger.search(
            limit=limit,
            actor_user_id=principal.user_id,
            log_access=True,
            verify_integrity=False,
        )
        return audit_logger.verify_events(events)

    if gdpr_handler is not None:

        @router.post("/compliance/gdpr/export")
        async def trigger_gdpr_export(
            request: Request,
            body: GDPRExportRequest,
        ) -> dict[str, Any]:
            """Execute GDPR Article 15 export for caller's own data or by compliance admin."""
            principal = await _authenticate_and_limit(request)
            is_admin = principal.has_permission("compliance:admin")
            if not is_admin and principal.user_id != body.user_id:
                raise HTTPException(
                    status_code=403,
                    detail="Data subjects may only export their own data",
                )
            out_dir = gdpr_export_dir or Path(tempfile.gettempdir()) / "pvs_gdpr_exports"
            archive_path = await gdpr_handler.export_user_data(
                user_id=body.user_id,
                requester_email=body.requester_email,
                output_dir=out_dir,
                export_format=body.export_format,
                actor_user_id=principal.user_id,
            )
            return {
                "status": "completed",
                "user_id": body.user_id,
                "archive_name": archive_path.name,
                "size_bytes": archive_path.stat().st_size,
            }

        @router.post("/compliance/gdpr/delete")
        async def trigger_gdpr_delete(
            request: Request,
            body: GDPRDeleteRequest,
        ) -> dict[str, Any]:
            """Execute GDPR Article 17 erasure for caller's own data or by compliance admin."""
            principal = await _authenticate_and_limit(request)
            is_admin = principal.has_permission("compliance:admin")
            if not is_admin and principal.user_id != body.user_id:
                raise HTTPException(
                    status_code=403,
                    detail="Data subjects may only delete their own data",
                )
            if not body.retain_audit_logs and not is_admin:
                raise HTTPException(
                    status_code=403,
                    detail="Only compliance:admin may request audit log subject anonymization",
                )
            counts = await gdpr_handler.delete_user_data(
                user_id=body.user_id,
                reason=body.reason,
                retain_audit_logs=body.retain_audit_logs,
                actor_user_id=principal.user_id,
            )
            return {
                "status": "completed",
                "user_id": body.user_id,
                "deletion_counts": counts,
                "audit_logs_retained": body.retain_audit_logs,
            }

    return router


__all__ = [
    "AuditPrincipal",
    "GDPRDeleteRequest",
    "GDPRExportRequest",
    "SlidingWindowRateLimiter",
    "create_audit_router",
]
