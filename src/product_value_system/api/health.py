"""
Health check, readiness, SLO status, and Prometheus metrics HTTP endpoints (EVO-014).

Endpoints:
- ``GET /health``: Liveness probe (always 200 when process is responsive)
- ``GET /ready``: Readiness probe validating database, sandbox, and LLM checks (200 or 503)
- ``GET /metrics``: Prometheus exposition format with optional API-key protection
- ``GET /slos``: Current SLI compliance and error budget remaining across all SLOs
"""

from __future__ import annotations

import hmac
import inspect
import json
import os
import socket
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request, Response
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from prometheus_client import generate_latest

from product_value_system.__version__ import __version__
from product_value_system.metrics.devx_tracker import DevXTracker
from product_value_system.metrics.space_calculator import SPACECalculator
from product_value_system.monitoring.metrics import PVSMetrics, get_metrics
from product_value_system.monitoring.slo import DEFAULT_SLOS, SLO
from product_value_system.monitoring.slo_evaluator import SLOEvaluator

if TYPE_CHECKING:
    from product_value_system.api.routes.audit import AuditPrincipal
    from product_value_system.compliance.audit_logger import AuditLogger
    from product_value_system.compliance.gdpr_handler import GDPRHandler

CheckCallable = Callable[[], Any]

_DEFAULT_CSP = "default-src 'self'; frame-ancestors 'none'"


def verify_api_key(request: Request, required_key: str | None) -> bool:
    """Verify ``X-API-Key`` or ``Authorization: Bearer`` using constant-time comparison."""
    if not required_key:
        return True
    provided = request.headers.get("x-api-key") or ""
    auth_header = request.headers.get("authorization") or ""
    bearer = auth_header.removeprefix("Bearer ").strip()
    return hmac.compare_digest(provided, required_key) or hmac.compare_digest(
        bearer, required_key
    )


def install_security_headers(app: FastAPI, *, csp: str = _DEFAULT_CSP) -> None:
    """Attach standard OWASP HTTP security headers middleware to a FastAPI app."""

    @app.middleware("http")
    async def add_security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Content-Security-Policy", csp)
        return response


def install_tracing_middleware(app: FastAPI) -> None:
    """Attach lightweight OpenTelemetry HTTP request tracing middleware (PROD-007)."""

    @app.middleware("http")
    async def trace_http_requests(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        tracer = trace.get_tracer("product_value_system.api")
        span_name = f"http.{request.method} {request.url.path}"
        with tracer.start_as_current_span(span_name) as span:
            span.set_attribute("http.method", request.method)
            span.set_attribute("http.route", request.url.path)
            try:
                response = await call_next(request)
                span.set_attribute("http.status_code", response.status_code)
                if response.status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR, f"HTTP {response.status_code}"))
                else:
                    span.set_status(Status(StatusCode.OK))
                return response
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                raise


async def _run_check(check: CheckCallable | None, default_detail: str) -> dict[str, Any]:
    """Execute an optional sync/async dependency health check safely."""
    if check is None:
        return {"status": "ok", "detail": default_detail}
    try:
        res = check()
        if inspect.isawaitable(res):
            res = await res
        if res is False:
            return {"status": "error", "message": "Check returned False"}
        if isinstance(res, dict):
            return res
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def create_app(
    *,
    enable_metrics: bool = True,
    metrics: PVSMetrics | None = None,
    slos: dict[str, SLO] | None = None,
    db_check: CheckCallable | None = None,
    sandbox_check: CheckCallable | None = None,
    llm_check: CheckCallable | None = None,
    metrics_api_key: str | None = None,
    devx_tracker: DevXTracker | None = None,
    audit_logger: AuditLogger | None = None,
    gdpr_handler: GDPRHandler | None = None,
    audit_principals: Mapping[str, AuditPrincipal] | None = None,
    gdpr_export_dir: Path | None = None,
    audit_rate_limit_per_hour: int = 100,
    enable_tracing: bool = True,
    startup_check: CheckCallable | None = None,
    pool_stats_provider: Callable[[], dict[str, Any]] | None = None,
    pod_name: str | None = None,
) -> FastAPI:
    """
    Build the FastAPI monitoring/health application for production deployments.

    Args:
        enable_metrics: Expose ``/metrics`` Prometheus scrape endpoint
        metrics: Optional ``PVSMetrics`` instance (defaults to global singleton)
        slos: Optional SLO catalog for ``/slos`` endpoint
        db_check: Optional sync/async callable validating database connectivity
        sandbox_check: Optional sync/async callable validating sandbox readiness
        llm_check: Optional sync/async callable validating LLM adapter readiness
        metrics_api_key: Optional API key guarding ``/metrics`` (or ``PVS_METRICS_API_KEY`` env)
    """
    active_metrics = metrics or get_metrics()
    evaluator = SLOEvaluator(active_metrics)
    active_slos = slos if slos is not None else DEFAULT_SLOS
    required_key = (
        metrics_api_key if metrics_api_key is not None else os.getenv("PVS_METRICS_API_KEY")
    )

    app = FastAPI(
        title="AI-Native Product Value System — Monitoring & Health API",
        version=__version__,
    )
    install_security_headers(app)
    if enable_tracing:
        install_tracing_middleware(app)

    started_monotonic = time.monotonic()
    resolved_pod_name = pod_name or os.getenv("POD_NAME") or socket.gethostname()

    @app.get("/health")
    @app.get("/health/live")
    async def health(response: Response) -> dict[str, str]:
        """Liveness probe returning 200 OK when the service process is alive."""
        response.headers["X-Pod-Name"] = resolved_pod_name
        return {
            "status": "healthy",
            "version": __version__,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    @app.get("/health/startup")
    async def startup() -> Response:
        """Startup probe verifying initialization (config, schemas, migrations)."""
        checks = {
            "config": {"status": "ok", "detail": "loaded"},
            "startup": await _run_check(startup_check, "initialized"),
        }
        all_ok = all(item.get("status") == "ok" for item in checks.values())
        payload = {
            "status": "started" if all_ok else "starting",
            "checks": checks,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return Response(
            content=json.dumps(payload),
            status_code=200 if all_ok else 503,
            media_type="application/json",
            headers={"X-Pod-Name": resolved_pod_name},
        )

    @app.get("/health/metrics-summary")
    async def metrics_summary(response: Response) -> dict[str, Any]:
        """Human-readable service health & connection pool summary."""
        response.headers["X-Pod-Name"] = resolved_pod_name
        snap = active_metrics.get_snapshot()
        return {
            "pod_name": resolved_pod_name,
            "uptime_seconds": round(time.monotonic() - started_monotonic, 2),
            "requests_total": snap.get("total_requests", 0),
            "errors_total": snap.get("total_errors", 0),
            "avg_latency_ms": round(float(snap.get("avg_latency_seconds", 0.0)) * 1000.0, 2),
            "database_pool": pool_stats_provider() if pool_stats_provider is not None else {},
            "timestamp": datetime.now(UTC).isoformat(),
        }

    @app.get("/ready")
    @app.get("/health/ready")
    async def ready() -> Response:
        """Readiness probe verifying database, sandbox, and LLM dependencies."""
        checks = {
            "database": await _run_check(db_check, "configured"),
            "sandbox": await _run_check(sandbox_check, "configured"),
            "llm": await _run_check(llm_check, "configured"),
        }
        all_ok = all(item.get("status") == "ok" for item in checks.values())
        payload = {
            "status": "ready" if all_ok else "not_ready",
            "checks": checks,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return Response(
            content=json.dumps(payload),
            status_code=200 if all_ok else 503,
            media_type="application/json",
            headers={"X-Pod-Name": resolved_pod_name},
        )

    @app.get("/slos")
    async def get_slos() -> dict[str, Any]:
        """Return live SLI compliance and remaining error budget per SLO."""
        statuses = await evaluator.evaluate_all(active_slos)
        return {
            "slos": {name: st.to_dict() for name, st in statuses.items()},
            "metrics_snapshot": active_metrics.get_snapshot(),
        }

    if enable_metrics:

        @app.get("/metrics")
        async def prometheus_metrics(request: Request) -> Response:
            """Expose Prometheus scrape endpoint with optional API-key authentication."""
            if not verify_api_key(request, required_key):
                if audit_logger is not None:
                    from product_value_system.compliance.audit_logger import (
                        AuditEvent,
                        AuditEventType,
                    )

                    await audit_logger.log(
                        AuditEvent(
                            event_type=AuditEventType.USER_LOGIN_FAILED,
                            timestamp=datetime.now(UTC),
                            user_id=None,
                            subject_id=None,
                            action="Unauthorized /metrics scrape attempt",
                            resource_type="metrics",
                            resource_id="/metrics",
                            result="failure",
                            metadata={},
                            client_ip=request.client.host if request.client else None,
                            user_agent=request.headers.get("user-agent"),
                        )
                    )
                return Response(
                    content=json.dumps({"error": "Unauthorized metrics scrape"}),
                    status_code=401,
                    media_type="application/json",
                )

            body, content_type = active_metrics.render_prometheus_metrics()
            if devx_tracker is not None and devx_tracker.enabled:
                devx_bytes = generate_latest(devx_tracker.registry)
                body = body.rstrip() + b"\n" + devx_bytes
            return Response(content=body, status_code=200, media_type=content_type)

    if devx_tracker is not None:
        space_calculator = SPACECalculator(tracker=devx_tracker)

        @app.get("/devx")
        async def get_devx_summary() -> dict[str, Any]:
            """Return aggregate-only SPACE, DORA, and AI effectiveness metrics (zero PII)."""
            return {
                "enabled": devx_tracker.enabled,
                "aggregate_only": devx_tracker.aggregate_only,
                "space_scores": await space_calculator.compute_space_scores(),
                "dora_metrics": devx_tracker.get_dora_metrics().to_dict(),
                "ai_effectiveness": devx_tracker.get_ai_effectiveness(),
            }

    if audit_logger is not None:
        from product_value_system.api.routes.audit import create_audit_router

        app.include_router(
            create_audit_router(
                audit_logger,
                principals_by_key=audit_principals or {},
                gdpr_handler=gdpr_handler,
                gdpr_export_dir=gdpr_export_dir,
                rate_limit_per_hour=audit_rate_limit_per_hour,
            )
        )

    return app


app = create_app()
