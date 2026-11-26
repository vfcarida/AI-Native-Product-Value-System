"""
FastAPI routes exposing open agent interoperability protocols (EVO-P3-07):
- A2A Agent Card discovery: ``GET /.well-known/agent.json``
- A2A Specialist Cards: ``GET /api/v1/a2a/agents`` and ``GET /api/v1/a2a/agents/{name}/card``
- A2A Multi-Tenant Tasks: ``POST /api/v1/a2a/tasks/send`` and ``GET /api/v1/a2a/tasks/{task_id}``
- Cedar-Governed MCP JSON-RPC 2.0: ``POST /api/v1/mcp``
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
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
from product_value_system.protocols.a2a import (
    A2AMessage,
    A2ARegistry,
    A2ATaskDispatcher,
)
from product_value_system.protocols.mcp import MCPServer


class A2ASendTaskRequest(BaseModel):
    """Accepts direct A2A task send payloads or JSON-RPC 2.0 envelopes."""

    id: str | None = None
    agent: str = Field(default="discovery-agent", max_length=120)
    message: dict[str, Any] | None = None
    require_human_approval: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Optional JSON-RPC 2.0 wrapper fields
    jsonrpc: str | None = None
    method: str | None = None
    params: dict[str, Any] | None = None


def create_protocols_router(
    *,
    mcp_server: MCPServer,
    a2a_registry: A2ARegistry,
    a2a_dispatcher: A2ATaskDispatcher,
    authenticator: ControlPlaneAuthenticator,
    rate_limiter: DistributedRateLimiter | None = None,
    audit_logger: AuditLogger | None = None,
) -> APIRouter:
    """Create FastAPI router exposing A2A discovery/tasks and Cedar-governed MCP JSON-RPC."""
    router = APIRouter(tags=["protocols"])
    limiter = rate_limiter or DistributedRateLimiter()

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
            raise HTTPException(
                status_code=403,
                detail=f"Missing required permission '{permission}'",
            )
        return ctx

    @router.get("/.well-known/agent.json")
    async def well_known_agent_card(request: Request) -> dict[str, Any]:
        """Standard public A2A Agent Card discovery endpoint."""
        base_url = str(request.base_url).rstrip("/")
        return a2a_registry.get_platform_card(base_url).to_dict()

    @router.get("/api/v1/a2a/agents")
    async def list_a2a_agents(request: Request, response: Response) -> dict[str, Any]:
        ctx = await _require(request, response, "agents:read")
        base_url = str(request.base_url).rstrip("/")
        cards = [c.to_dict() for c in a2a_registry.list_agent_cards(base_url)]
        return {
            "tenant_id": ctx.tenant_id,
            "count": len(cards),
            "agents": cards,
        }

    @router.get("/api/v1/a2a/agents/{agent_name}/card")
    async def get_a2a_agent_card(
        agent_name: str,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        await _require(request, response, "agents:read")
        base_url = str(request.base_url).rstrip("/")
        card = a2a_registry.get_agent_card(agent_name, base_url)
        if card is None:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
        return card.to_dict()

    @router.post("/api/v1/a2a/tasks/send", status_code=201)
    async def send_a2a_task(
        payload: A2ASendTaskRequest,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        ctx = await _require(request, response, "runs:write")
        is_jsonrpc = payload.jsonrpc == "2.0" and payload.params is not None
        params = payload.params if is_jsonrpc and payload.params is not None else {}

        raw_msg = params.get("message") if is_jsonrpc else payload.message
        if not isinstance(raw_msg, dict) or not isinstance(raw_msg.get("parts"), list):
            raise HTTPException(
                status_code=422,
                detail="A2A task requires message object with 'parts' list",
            )

        agent_name = str(params.get("agent") or payload.agent)
        task_id = params.get("id") if is_jsonrpc else payload.id
        require_approval = bool(
            params.get("require_human_approval", payload.require_human_approval)
        )
        metadata = dict(params.get("metadata") or payload.metadata)

        task = await a2a_dispatcher.send_task(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            message=A2AMessage(
                role=str(raw_msg.get("role", "user")),
                parts=list(raw_msg["parts"]),
            ),
            agent_name=agent_name,
            task_id=str(task_id) if task_id else None,
            require_human_approval=require_approval,
            metadata=metadata,
        )

        if audit_logger is not None:
            await audit_logger.log(
                AuditEvent(
                    event_type=AuditEventType.AGENT_STARTED,
                    timestamp=datetime.now(UTC),
                    user_id=ctx.user_id,
                    subject_id=None,
                    action=f"Dispatched A2A task {task.id} to {agent_name}",
                    resource_type="a2a_task",
                    resource_id=task.id,
                    result="success",
                    metadata={"tenant_id": ctx.tenant_id, "state": task.state.value},
                )
            )

        if is_jsonrpc:
            return {"jsonrpc": "2.0", "id": payload.id, "result": task.to_dict()}
        return task.to_dict()

    @router.get("/api/v1/a2a/tasks/{task_id}")
    async def get_a2a_task(
        task_id: str,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        ctx = await _require(request, response, "runs:read")
        task = await a2a_dispatcher.get_task(task_id, tenant_id=ctx.tenant_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"A2A task '{task_id}' not found")
        return task.to_dict()

    @router.post("/api/v1/mcp")
    async def mcp_jsonrpc_endpoint(
        payload: dict[str, Any],
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        """Authenticated, Cedar-governed MCP JSON-RPC 2.0 endpoint."""
        method = str(payload.get("method", ""))
        required_perm = "runs:write" if method == "tools/call" else "agents:read"
        ctx = await _require(request, response, required_perm)
        agent_name = (request.headers.get("x-agent-name") or "discovery").strip() or "discovery"
        return await mcp_server.handle_jsonrpc(
            payload,
            agent_name=agent_name,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
        )

    return router
