"""
Health check, readiness, SLO status, and Prometheus metrics HTTP endpoints (EVO-014).

Endpoints:
- ``GET /health``: Liveness probe (always 200 when process is responsive)
- ``GET /ready``: Readiness probe validating database, sandbox, and LLM checks (200 or 503)
- ``GET /metrics``: Prometheus exposition format with optional API-key protection
- ``GET /slos``: Current SLI compliance and error budget remaining across all SLOs
"""

import hmac
import inspect
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from prometheus_client import generate_latest

from product_value_system.__version__ import __version__
from product_value_system.metrics.devx_tracker import DevXTracker
from product_value_system.metrics.space_calculator import SPACECalculator
from product_value_system.monitoring.metrics import PVSMetrics, get_metrics
from product_value_system.monitoring.slo import DEFAULT_SLOS, SLO
from product_value_system.monitoring.slo_evaluator import SLOEvaluator

CheckCallable = Callable[[], Any]


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
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.get("/health")
    @app.get("/health/live")
    async def health() -> dict[str, str]:
        """Liveness probe returning 200 OK when the service process is alive."""
        return {"status": "healthy", "version": __version__}

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
        }
        return Response(
            content=json.dumps(payload),
            status_code=200 if all_ok else 503,
            media_type="application/json",
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
            if required_key:
                provided = request.headers.get("x-api-key") or ""
                auth_header = request.headers.get("authorization") or ""
                bearer = auth_header.removeprefix("Bearer ").strip()
                valid_header = hmac.compare_digest(provided, required_key)
                valid_bearer = hmac.compare_digest(bearer, required_key)
                if not (valid_header or valid_bearer):
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

    return app


app = create_app()
