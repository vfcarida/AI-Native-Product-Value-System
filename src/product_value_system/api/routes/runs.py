"""
Control Plane REST API for multi-tenant Value Stream Runs, durable HITL
approvals, and Agent Registry introspection (EVO-P3-02 & EVO-P3-05).

Endpoints:
- ``POST /api/v1/runs``: Create and start a tenant-scoped value stream run
- ``GET  /api/v1/runs``: List runs belonging to the authenticated tenant
- ``GET  /api/v1/runs/{run_id}``: Inspect run status, stages, artifacts, and approvals
- ``POST /api/v1/runs/{run_id}/approve``: Approve pending HITL gate and resume run
- ``POST /api/v1/runs/{run_id}/reject``: Reject pending HITL gate and cancel run
- ``GET  /api/v1/agents``: List registered PVS agents, capabilities, and schemas
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from product_value_system.api.auth import (
    ControlPlaneAuthenticator,
    DistributedRateLimiter,
    TenantContext,
)
from product_value_system.compliance.audit_logger import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
)
from product_value_system.persistence.run_store import (
    ApprovalRecord,
    DurableRunRecord,
    DurableRunStore,
    new_approval_id,
    new_run_id,
)

DEFAULT_AGENT_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "name": "discovery-agent",
        "version": "1.0.0",
        "stage": "discovery",
        "capabilities": ["problem_analysis", "evidence_synthesis", "opportunity_mapping"],
    },
    {
        "name": "specification-agent",
        "version": "1.0.0",
        "stage": "specification",
        "capabilities": ["executable_specs", "acceptance_criteria", "contract_validation"],
    },
    {
        "name": "architecture-review-agent",
        "version": "1.0.0",
        "stage": "review",
        "capabilities": ["architecture_review", "boundary_analysis", "adr_compliance"],
    },
    {
        "name": "security-review-agent",
        "version": "1.0.0",
        "stage": "review",
        "capabilities": ["owasp_review", "cedar_policy_check", "secret_scanning"],
    },
    {
        "name": "build-agent",
        "version": "1.0.0",
        "stage": "build",
        "capabilities": ["code_generation", "test_generation", "sandbox_execution"],
    },
    {
        "name": "brownfield-agent",
        "version": "1.0.0",
        "stage": "modernization",
        "capabilities": ["git_forensics", "modularity_detection", "strangler_fig_acl"],
    },
    {
        "name": "measurement-agent",
        "version": "1.0.0",
        "stage": "learn",
        "capabilities": ["outcome_tracking", "ab_evaluation", "feedback_synthesis"],
    },
)


class CreateRunRequest(BaseModel):
    problem_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=10_000)
    require_human_approval: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    comment: str = Field(default="", max_length=1000)


async def _log_audit(
    audit_logger: AuditLogger | None,
    *,
    event_type: AuditEventType,
    ctx: TenantContext,
    action: str,
    resource_id: str,
    result: str,
    metadata: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    if audit_logger is None:
        return
    await audit_logger.log(
        AuditEvent(
            event_type=event_type,
            timestamp=datetime.now(UTC),
            user_id=ctx.user_id,
            subject_id=None,
            action=action,
            resource_type="value_stream_run",
            resource_id=resource_id,
            result=result,
            metadata={"tenant_id": ctx.tenant_id, **(metadata or {})},
            client_ip=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
    )


def create_control_plane_router(
    *,
    run_store: DurableRunStore,
    authenticator: ControlPlaneAuthenticator,
    rate_limiter: DistributedRateLimiter | None = None,
    audit_logger: AuditLogger | None = None,
    agents_catalog: list[dict[str, Any]] | None = None,
) -> APIRouter:
    """Create authenticated, tenant-isolated Control Plane REST API router."""
    router = APIRouter(prefix="/api/v1", tags=["control-plane"])
    limiter = rate_limiter or DistributedRateLimiter()
    catalog = list(agents_catalog) if agents_catalog is not None else list(DEFAULT_AGENT_CATALOG)

    async def _require(request: Request, response: Response, permission: str) -> TenantContext:
        ctx = authenticator.authenticate(request)
        response.headers["X-Tenant-ID"] = ctx.tenant_id

        allowed, retry_after = await limiter.check_and_record(f"{ctx.tenant_id}:{ctx.user_id}")
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        if not ctx.has_permission(permission):
            await _log_audit(
                audit_logger,
                event_type=AuditEventType.USER_LOGIN_FAILED,
                ctx=ctx,
                action=f"Denied missing permission '{permission}'",
                resource_id=request.url.path,
                result="denied",
                request=request,
            )
            raise HTTPException(
                status_code=403,
                detail=f"Missing required permission '{permission}'",
            )
        return ctx

    @router.get("/agents")
    async def list_agents(request: Request, response: Response) -> dict[str, Any]:
        ctx = await _require(request, response, "agents:read")
        return {
            "tenant_id": ctx.tenant_id,
            "count": len(catalog),
            "agents": catalog,
        }

    @router.post("/runs", status_code=201)
    async def create_run(
        payload: CreateRunRequest,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        ctx = await _require(request, response, "runs:write")
        now = datetime.now(UTC)
        run_id = new_run_id()

        stages: dict[str, dict[str, Any]] = {
            "problem_analysis": {
                "status": "completed",
                "completed_at": now.isoformat(),
            },
            "discovery": {
                "status": "completed",
                "completed_at": now.isoformat(),
            },
        }

        if payload.require_human_approval:
            initial_status = "waiting_approval"
            current_stage = "decision_gate"
            completed_at = None
            stages["decision_gate"] = {
                "status": "waiting_approval",
                "requested_at": now.isoformat(),
            }
        else:
            initial_status = "completed"
            current_stage = "completed"
            completed_at = now
            stages["decision_gate"] = {"status": "skipped", "completed_at": now.isoformat()}
            stages["specification"] = {"status": "completed", "completed_at": now.isoformat()}

        record = DurableRunRecord(
            run_id=run_id,
            tenant_id=ctx.tenant_id,
            problem_id=payload.problem_id,
            problem_payload={
                "title": payload.title,
                "description": payload.description,
                "metadata": payload.metadata,
            },
            status=initial_status,
            current_stage=current_stage,
            created_by=ctx.user_id,
            created_at=now,
            updated_at=now,
            completed_at=completed_at,
            stages=stages,
        )
        await run_store.create_run(record)

        if payload.require_human_approval:
            approval = ApprovalRecord(
                approval_id=new_approval_id(),
                run_id=run_id,
                tenant_id=ctx.tenant_id,
                stage_name="decision_gate",
                status="pending",
                requested_at=now,
            )
            await run_store.create_approval(approval)
            record.approvals.append(approval)

        await _log_audit(
            audit_logger,
            event_type=AuditEventType.AGENT_STARTED,
            ctx=ctx,
            action=f"Created value stream run {run_id} (status={initial_status})",
            resource_id=run_id,
            result="success",
            metadata={"problem_id": payload.problem_id, "status": initial_status},
            request=request,
        )
        return record.to_dict()

    @router.get("/runs")
    async def list_runs(
        request: Request,
        response: Response,
        status: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        ctx = await _require(request, response, "runs:read")
        runs = await run_store.list_runs(
            tenant_id=ctx.tenant_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return {
            "tenant_id": ctx.tenant_id,
            "count": len(runs),
            "runs": [r.to_dict() for r in runs],
        }

    @router.get("/runs/{run_id}")
    async def get_run(
        run_id: str,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        ctx = await _require(request, response, "runs:read")
        rec = await run_store.get_run(run_id, tenant_id=ctx.tenant_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        return rec.to_dict()

    @router.post("/runs/{run_id}/approve")
    async def approve_run(
        run_id: str,
        body: ApprovalDecisionRequest,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        ctx = await _require(request, response, "runs:approve")
        rec = await run_store.get_run(run_id, tenant_id=ctx.tenant_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        if rec.status != "waiting_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Run '{run_id}' is not waiting for approval (status={rec.status})",
            )

        resolved = await run_store.resolve_approval(
            run_id=run_id,
            tenant_id=ctx.tenant_id,
            decision="approved",
            decided_by=ctx.user_id,
            comment=body.comment,
        )
        if resolved is None:
            raise HTTPException(status_code=409, detail="No pending approval gate found")

        now = datetime.now(UTC)
        rec.stages["decision_gate"] = {
            "status": "completed",
            "approved_by": ctx.user_id,
            "completed_at": now.isoformat(),
        }
        rec.stages["specification"] = {"status": "completed", "completed_at": now.isoformat()}
        rec.status = "completed"
        rec.current_stage = "completed"
        rec.completed_at = now
        await run_store.update_run(rec)

        updated = await run_store.get_run(run_id, tenant_id=ctx.tenant_id)
        assert updated is not None

        await _log_audit(
            audit_logger,
            event_type=AuditEventType.AGENT_COMPLETED,
            ctx=ctx,
            action=f"Approved HITL gate for run {run_id}",
            resource_id=run_id,
            result="success",
            metadata={"approval_id": resolved.approval_id, "comment": body.comment},
            request=request,
        )
        return updated.to_dict()

    @router.post("/runs/{run_id}/reject")
    async def reject_run(
        run_id: str,
        body: ApprovalDecisionRequest,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        ctx = await _require(request, response, "runs:approve")
        rec = await run_store.get_run(run_id, tenant_id=ctx.tenant_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        if rec.status != "waiting_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Run '{run_id}' is not waiting for approval (status={rec.status})",
            )

        resolved = await run_store.resolve_approval(
            run_id=run_id,
            tenant_id=ctx.tenant_id,
            decision="rejected",
            decided_by=ctx.user_id,
            comment=body.comment,
        )
        if resolved is None:
            raise HTTPException(status_code=409, detail="No pending approval gate found")

        now = datetime.now(UTC)
        rec.stages["decision_gate"] = {
            "status": "rejected",
            "rejected_by": ctx.user_id,
            "completed_at": now.isoformat(),
        }
        rec.status = "cancelled"
        rec.current_stage = "decision_gate"
        rec.completed_at = now
        await run_store.update_run(rec)

        updated = await run_store.get_run(run_id, tenant_id=ctx.tenant_id)
        assert updated is not None

        await _log_audit(
            audit_logger,
            event_type=AuditEventType.AGENT_FAILED,
            ctx=ctx,
            action=f"Rejected HITL gate for run {run_id}",
            resource_id=run_id,
            result="denied",
            metadata={"approval_id": resolved.approval_id, "comment": body.comment},
            request=request,
        )
        return updated.to_dict()

    return router
