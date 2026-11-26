"""
Production ASGI composition root with FastAPI lifespan management (EVO-P3-01).

Wires together all production subsystems into a single runnable ASGI app:
- Real database connectivity (PostgreSQL HA via ``DATABASE_URL`` or SQLite WAL)
- Real ``/health/ready`` and ``/health/startup`` probes (returns 503 if DB down)
- Live ``/health/metrics-summary`` connection pool telemetry
- OpenTelemetry distributed tracing initialization (``init_tracing()``)
- Immutable HMAC-signed ``AuditLogger`` & ``GDPRHandler`` (``/api/v1/audit/*``)
- Non-punitive SPACE/DORA ``DevXTracker`` (``/devx``)
- Multi-tenant Control Plane REST API (``/api/v1/runs``, ``/approve``, ``/agents``)
- Graceful connection pool draining on ``SIGTERM``
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from product_value_system.api.auth import (
    DEFAULT_ROLE_PERMISSIONS,
    ControlPlaneAuthenticator,
    DistributedRateLimiter,
    TenantContext,
)
from product_value_system.api.health import create_app
from product_value_system.api.routes.audit import AuditPrincipal
from product_value_system.api.routes.protocols import create_protocols_router
from product_value_system.api.routes.runs import create_control_plane_router
from product_value_system.compliance.audit_logger import AuditLogger, InMemoryAuditStore
from product_value_system.compliance.gdpr_handler import GDPRHandler
from product_value_system.compliance.signing import generate_signing_key
from product_value_system.harness.tools import ToolRegistry
from product_value_system.metrics.devx_tracker import DevXTracker
from product_value_system.monitoring.metrics import PVSMetrics
from product_value_system.observability.tracing import init_tracing
from product_value_system.persistence.postgres import PostgresDatabase
from product_value_system.persistence.run_store import (
    DurableRunStore,
    PostgresDurableRunStore,
    SQLiteDurableRunStore,
)
from product_value_system.policy.engine import PolicyEngine
from product_value_system.protocols.a2a import A2ARegistry, A2ATaskDispatcher
from product_value_system.protocols.mcp import MCPServer

logger = logging.getLogger(__name__)


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ProductionSettings:
    """Environment-driven settings for the production ASGI control plane."""

    database_url: str = ""
    redis_url: str = ""
    metrics_api_key: str = ""
    jwt_secret: str = ""
    jwt_public_key: str = ""
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    audit_signing_key: str = ""
    github_webhook_secret: str = ""
    slack_signing_secret: str = ""
    enable_devx: bool = True
    enable_audit: bool = True
    enable_tracing: bool = True
    enable_control_plane: bool = True
    enable_protocols: bool = True
    enable_integrations: bool = True
    allow_anonymous_dev: bool = False
    rate_limit_per_minute: int = 120
    api_keys: dict[str, TenantContext] = field(default_factory=dict)
    audit_principals: dict[str, AuditPrincipal] = field(default_factory=dict)
    gdpr_export_dir: Path = field(
        default_factory=lambda: Path(tempfile.gettempdir()) / "pvs-gdpr-exports"
    )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ProductionSettings:
        environ = os.environ if env is None else env
        default_sqlite = str(Path(tempfile.gettempdir()) / "pvs-control-plane.db")
        db_url = environ.get("DATABASE_URL") or f"sqlite:///{default_sqlite}"

        api_keys: dict[str, TenantContext] = {}
        audit_principals: dict[str, AuditPrincipal] = {}
        raw_keys = environ.get("PVS_API_KEYS_JSON", "").strip()
        if raw_keys:
            try:
                parsed = json.loads(raw_keys)
                if isinstance(parsed, dict):
                    for key_val, meta in parsed.items():
                        if not isinstance(meta, dict):
                            continue
                        role = str(meta.get("role", "developer")).lower()
                        perms_raw = meta.get("permissions")
                        perms = (
                            frozenset(str(p) for p in perms_raw)
                            if isinstance(perms_raw, list)
                            else DEFAULT_ROLE_PERMISSIONS.get(
                                role, DEFAULT_ROLE_PERMISSIONS["viewer"]
                            )
                        )
                        api_keys[str(key_val)] = TenantContext(
                            tenant_id=str(meta.get("tenant_id", "default")),
                            user_id=str(meta.get("user_id", "api-user")),
                            role=role,
                            permissions=perms,
                            auth_method="api_key",
                        )
                        audit_principals[str(key_val)] = AuditPrincipal(
                            user_id=str(meta.get("user_id", "api-user")),
                            role=role,
                            permissions=perms,
                        )
            except json.JSONDecodeError as exc:
                logger.warning("Ignoring malformed PVS_API_KEYS_JSON: %s", exc)

        return cls(
            database_url=db_url,
            redis_url=environ.get("REDIS_URL", ""),
            metrics_api_key=environ.get("PVS_METRICS_API_KEY", ""),
            jwt_secret=environ.get("PVS_JWT_SECRET", ""),
            jwt_public_key=environ.get("PVS_JWT_PUBLIC_KEY", ""),
            jwt_issuer=environ.get("PVS_JWT_ISSUER") or None,
            jwt_audience=environ.get("PVS_JWT_AUDIENCE") or None,
            audit_signing_key=environ.get("PVS_AUDIT_SIGNING_KEY", ""),
            github_webhook_secret=environ.get("PVS_GITHUB_WEBHOOK_SECRET", ""),
            slack_signing_secret=environ.get("PVS_SLACK_SIGNING_SECRET", ""),
            enable_devx=_parse_bool(environ.get("PVS_ENABLE_DEVX"), True),
            enable_audit=_parse_bool(environ.get("PVS_ENABLE_AUDIT"), True),
            enable_tracing=_parse_bool(environ.get("PVS_ENABLE_TRACING"), True),
            enable_control_plane=_parse_bool(environ.get("PVS_ENABLE_CONTROL_PLANE"), True),
            enable_protocols=_parse_bool(environ.get("PVS_ENABLE_PROTOCOLS"), True),
            enable_integrations=_parse_bool(environ.get("PVS_ENABLE_INTEGRATIONS"), True),
            allow_anonymous_dev=_parse_bool(environ.get("PVS_ALLOW_ANONYMOUS_DEV"), False),
            rate_limit_per_minute=int(environ.get("PVS_RATE_LIMIT_PER_MINUTE", "120")),
            api_keys=api_keys,
            audit_principals=audit_principals,
        )


def _build_run_store(database_url: str) -> DurableRunStore:
    if database_url.startswith(("postgresql://", "postgres://")):
        pg_db = PostgresDatabase.from_env(dsn=database_url)
        return PostgresDurableRunStore(pg_db)
    if database_url == ":memory:":
        return SQLiteDurableRunStore(":memory:")
    sqlite_path = database_url.removeprefix("sqlite:///")
    return SQLiteDurableRunStore(sqlite_path)


class _DefaultKnowledgeBaseTool:
    """Built-in safe read-only knowledge base tool exposed via Cedar-governed MCP."""

    @property
    def name(self) -> str:
        return "knowledge_base"

    @property
    def description(self) -> str:
        return "Query Product Value System architecture & operational knowledge base."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        query = str(kwargs.get("query", "")).strip()
        return {
            "query": query,
            "matches": [
                {
                    "source": "pvs-architecture",
                    "summary": f"Indexed PDLC/SDLC knowledge result for: {query}",
                }
            ],
        }


def create_production_app(
    settings: ProductionSettings | None = None,
    *,
    run_store: DurableRunStore | None = None,
    metrics: PVSMetrics | None = None,
    audit_logger: AuditLogger | None = None,
    gdpr_handler: GDPRHandler | None = None,
    devx_tracker: DevXTracker | None = None,
    redis_client: Any | None = None,
    tool_registry: ToolRegistry | None = None,
    mcp_server: MCPServer | None = None,
) -> FastAPI:
    """
    Build the fully wired production FastAPI application with managed ASGI lifespan.
    """
    cfg = settings or ProductionSettings.from_env()
    store: DurableRunStore = run_store or _build_run_store(cfg.database_url)
    active_metrics = metrics or PVSMetrics(register_global_prometheus=False)

    active_devx: DevXTracker | None = devx_tracker
    if active_devx is None and cfg.enable_devx:
        active_devx = DevXTracker(
            {"enabled": True, "privacy": {"aggregate_only": True, "anonymize": True}}
        )

    active_audit: AuditLogger | None = audit_logger
    if active_audit is None and cfg.enable_audit:
        signing_key: bytes | str = (
            cfg.audit_signing_key if cfg.audit_signing_key else generate_signing_key()
        )
        active_audit = AuditLogger(
            store=InMemoryAuditStore(),
            signing_key=signing_key,
            enable_async=False,
        )

    active_gdpr: GDPRHandler | None = gdpr_handler
    if active_gdpr is None and active_audit is not None:
        active_gdpr = GDPRHandler(audit_logger=active_audit)

    state: dict[str, Any] = {
        "started": False,
        "redis": redis_client,
    }

    async def _db_health_check() -> dict[str, Any]:
        healthy = await store.health_check()
        if healthy:
            return {"status": "ok", "pool": store.get_pool_stats()}
        return {"status": "error", "message": "Database connectivity check failed"}

    async def _startup_check() -> dict[str, Any]:
        if state["started"] and await store.health_check():
            return {"status": "ok", "detail": "lifespan_initialized"}
        return {"status": "error", "message": "Application lifespan not initialized"}

    def _pool_stats() -> dict[str, Any]:
        return store.get_pool_stats()

    app = create_app(
        metrics=active_metrics,
        metrics_api_key=cfg.metrics_api_key,
        db_check=_db_health_check,
        startup_check=_startup_check,
        pool_stats_provider=_pool_stats,
        devx_tracker=active_devx,
        audit_logger=active_audit,
        gdpr_handler=active_gdpr,
        audit_principals=cfg.audit_principals,
        gdpr_export_dir=cfg.gdpr_export_dir,
        enable_tracing=cfg.enable_tracing,
    )

    authenticator = ControlPlaneAuthenticator(
        api_keys=cfg.api_keys,
        jwt_secret=cfg.jwt_secret,
        jwt_public_key=cfg.jwt_public_key,
        jwt_issuer=cfg.jwt_issuer,
        jwt_audience=cfg.jwt_audience,
        allow_anonymous_dev=cfg.allow_anonymous_dev,
    )
    rate_limiter = DistributedRateLimiter(
        max_requests=cfg.rate_limit_per_minute,
        window_seconds=60.0,
        redis_client=redis_client,
    )

    if cfg.enable_control_plane:
        app.include_router(
            create_control_plane_router(
                run_store=store,
                authenticator=authenticator,
                rate_limiter=rate_limiter,
                audit_logger=active_audit,
            )
        )

    active_mcp: MCPServer | None = mcp_server
    a2a_registry = A2ARegistry()
    if cfg.enable_protocols:
        if active_mcp is None:
            resolved_registry = tool_registry or ToolRegistry(PolicyEngine())
            if resolved_registry.get_tool("knowledge_base") is None:
                resolved_registry.register(_DefaultKnowledgeBaseTool())
            active_mcp = MCPServer(
                resolved_registry,
                audit_logger=active_audit,
            )
        a2a_dispatcher = A2ATaskDispatcher(run_store=store, registry=a2a_registry)
        app.include_router(
            create_protocols_router(
                mcp_server=active_mcp,
                a2a_registry=a2a_registry,
                a2a_dispatcher=a2a_dispatcher,
                authenticator=authenticator,
                rate_limiter=rate_limiter,
                audit_logger=active_audit,
            )
        )

    if cfg.enable_integrations:
        from product_value_system.api.routes.integrations import create_integrations_router

        app.include_router(
            create_integrations_router(
                run_store=store,
                devx_tracker=active_devx,
                audit_logger=active_audit,
                github_webhook_secret=cfg.github_webhook_secret,
                slack_signing_secret=cfg.slack_signing_secret,
            )
        )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if cfg.enable_tracing:
            try:
                init_tracing()
            except Exception as exc:
                logger.warning("Tracing initialization degraded (%s)", exc)

        await store.initialize()

        if state["redis"] is None and cfg.redis_url:
            try:
                import redis.asyncio as aioredis

                state["redis"] = aioredis.from_url(cfg.redis_url)
                rate_limiter.set_redis_client(state["redis"])
            except Exception as exc:
                logger.warning("Redis connection skipped (%s); using local fallback", exc)

        state["started"] = True
        logger.info("PVS production control plane started (%s)", store.get_pool_stats())
        try:
            yield
        finally:
            state["started"] = False
            await store.close()
            if state["redis"] is not None:
                close_fn = getattr(state["redis"], "aclose", None) or getattr(
                    state["redis"], "close", None
                )
                if callable(close_fn):
                    res = close_fn()
                    if hasattr(res, "__await__"):
                        await res
            logger.info("PVS production control plane shut down cleanly")

    app.router.lifespan_context = _lifespan
    app.state.run_store = store
    app.state.settings = cfg
    app.state.mcp_server = active_mcp
    app.state.a2a_registry = a2a_registry
    return app


app = create_production_app()
