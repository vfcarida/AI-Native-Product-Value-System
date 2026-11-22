"""
FastAPI Admin UI & REST API for Feature Flags and Progressive Rollouts (EVO-015).

Endpoints:
- ``GET /health``: Liveness probe
- ``GET /admin/flags``: HTML dashboard of all feature flags
- ``GET /admin/flags/{flag_name}``: HTML detail page for a single flag
- ``GET /admin/api/flags``: JSON catalog of all flags
- ``POST /admin/flags/{flag_name}/toggle``: Toggle flag master switch on/off
- ``POST /admin/flags/{flag_name}/rollout``: Update rollout percentage and/or stage
- ``POST /admin/flags/{flag_name}/advance``: Advance stage via ``RolloutController``
- ``POST /admin/flags/{flag_name}/rollback``: Emergency rollback via ``RolloutController``
- ``GET /admin/audit``: Flag mutation audit trail
"""

from __future__ import annotations

import json
import os
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from product_value_system.__version__ import __version__
from product_value_system.api.health import install_security_headers, verify_api_key
from product_value_system.feature_flags.models import FeatureFlag, RolloutStage
from product_value_system.feature_flags.rollout import MetricsSLOMonitor, RolloutController
from product_value_system.feature_flags.service import FeatureFlagService

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _load_template(filename: str) -> str:
    return (TEMPLATES_DIR / filename).read_text(encoding="utf-8")


def _render_flags_dashboard(flags: list[FeatureFlag]) -> str:
    rows: list[str] = []
    for flag in flags:
        safe_name = escape(flag.name)
        status_cls = "badge-on" if flag.enabled else "badge-off"
        status_label = "ENABLED" if flag.enabled else "DISABLED"
        rows.append(
            "<tr>"
            f'<td><a href="/admin/flags/{safe_name}"><strong>{safe_name}</strong></a></td>'
            f'<td><span class="badge badge-type">{escape(flag.type.value)}</span></td>'
            f'<td><span class="badge {status_cls}">{status_label}</span></td>'
            f'<td><span class="badge badge-stage">{escape(flag.rollout_stage.value)}</span></td>'
            f"<td>{flag.rollout_percentage:.0%}</td>"
            f"<td>{escape(flag.description)}</td>"
            "<td>"
            f"<button onclick=\"toggleFlag('{safe_name}')\">Toggle</button> "
            f"<button onclick=\"advanceFlag('{safe_name}')\">Advance</button>"
            "</td>"
            "</tr>"
        )
    template = _load_template("flags.html")
    return template.replace("{{ROWS}}", "\n".join(rows))


def _render_flag_detail(flag: FeatureFlag, audit_entries: list[dict[str, Any]]) -> str:
    template = _load_template("flag_detail.html")
    replacements = {
        "{{FLAG_NAME}}": escape(flag.name),
        "{{DESCRIPTION}}": escape(flag.description or "No description provided."),
        "{{FLAG_TYPE}}": escape(flag.type.value),
        "{{ENABLED}}": "true" if flag.enabled else "false",
        "{{STAGE}}": escape(flag.rollout_stage.value),
        "{{PERCENTAGE}}": f"{flag.rollout_percentage:.0%}",
        "{{UPDATED_AT}}": escape(flag.to_dict()["updated_at"]),
        "{{TARGETING_RULES}}": escape(json.dumps(flag.targeting_rules, indent=2)),
        "{{AUDIT_LOG}}": escape(json.dumps(audit_entries, indent=2)),
    }
    rendered = template
    for key, val in replacements.items():
        rendered = rendered.replace(key, val)
    return rendered


def _check_admin_auth(request: Request, required_key: str | None) -> JSONResponse | None:
    if not verify_api_key(request, required_key):
        return JSONResponse(
            status_code=401,
            content={"error": "Unauthorized admin request"},
        )
    return None


def create_admin_app(
    flag_service: FeatureFlagService | None = None,
    *,
    rollout_controller: RolloutController | None = None,
    api_key: str | None = None,
    flags_file: Path | str | None = None,
) -> FastAPI:
    """Create FastAPI application for feature flag administration and progressive delivery."""
    if flag_service is None:
        resolved_file = (
            Path(flags_file)
            if flags_file is not None
            else Path(os.getenv("PVS_FLAGS_FILE", "flags.yaml"))
        )
        if resolved_file.exists():
            service = FeatureFlagService.from_yaml(resolved_file)
        else:
            service = FeatureFlagService(flags={}, storage_path=resolved_file)
    else:
        service = flag_service

    controller = rollout_controller or RolloutController(
        flag_service=service,
        slo_monitor=MetricsSLOMonitor(),
        auto_rollback_enabled=True,
    )
    required_key = api_key if api_key is not None else os.getenv("PVS_ADMIN_API_KEY")

    app = FastAPI(
        title="AI-Native Product Value System — Feature Flags Admin UI",
        version=__version__,
    )
    install_security_headers(
        app,
        csp=(
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
        ),
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy", "service": "feature-flags-admin", "version": __version__}

    @app.get("/admin/flags", response_class=HTMLResponse)
    async def list_flags_html() -> HTMLResponse:
        return HTMLResponse(content=_render_flags_dashboard(service.list_flags()), status_code=200)

    @app.get("/admin/api/flags")
    async def list_flags_json() -> dict[str, Any]:
        return {
            "flags": [flag.to_dict() for flag in service.list_flags()],
            "count": len(service.flags),
        }

    @app.get("/admin/flags/{flag_name}", response_class=HTMLResponse)
    async def get_flag_detail(flag_name: str) -> Response:
        flag = service.get_flag(flag_name)
        if flag is None:
            return JSONResponse(status_code=404, content={"error": "Flag not found"})
        entries = [
            entry.to_dict()
            for entry in service.get_audit_log(limit=50)
            if entry.flag_name == flag_name
        ]
        return HTMLResponse(content=_render_flag_detail(flag, entries), status_code=200)

    @app.post("/admin/flags/{flag_name}/toggle")
    async def toggle_flag(flag_name: str, request: Request) -> JSONResponse:
        auth_err = _check_admin_auth(request, required_key)
        if auth_err is not None:
            return auth_err
        actor = request.headers.get("x-actor", "admin-ui")
        try:
            flag = service.toggle_flag(flag_name, actor=actor)
        except KeyError:
            return JSONResponse(status_code=404, content={"error": "Flag not found"})
        return JSONResponse(
            status_code=200,
            content={
                "flag_name": flag.name,
                "enabled": flag.enabled,
                "rollout_stage": flag.rollout_stage.value,
                "rollout_percentage": flag.rollout_percentage,
            },
        )

    @app.post("/admin/flags/{flag_name}/rollout")
    async def update_rollout(flag_name: str, request: Request) -> JSONResponse:
        auth_err = _check_admin_auth(request, required_key)
        if auth_err is not None:
            return auth_err
        actor = request.headers.get("x-actor", "admin-ui")
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "JSON object body required"})

        percentage = body.get("percentage")
        stage_raw = body.get("stage")
        stage: RolloutStage | None = None
        if stage_raw is not None:
            try:
                stage = RolloutStage(str(stage_raw))
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Invalid rollout stage: {stage_raw}"},
                )

        try:
            flag = service.set_rollout(
                flag_name,
                percentage=float(percentage) if percentage is not None else None,
                stage=stage,
                actor=actor,
            )
        except KeyError:
            return JSONResponse(status_code=404, content={"error": "Flag not found"})

        return JSONResponse(
            status_code=200,
            content={
                "flag_name": flag.name,
                "rollout_stage": flag.rollout_stage.value,
                "rollout_percentage": flag.rollout_percentage,
                "enabled": flag.enabled,
            },
        )

    @app.post("/admin/flags/{flag_name}/advance")
    async def advance_flag(flag_name: str, request: Request) -> JSONResponse:
        auth_err = _check_admin_auth(request, required_key)
        if auth_err is not None:
            return auth_err
        actor = request.headers.get("x-actor", "admin-ui")
        try:
            result = await controller.advance_rollout(flag_name, actor=actor)
        except KeyError:
            return JSONResponse(status_code=404, content={"error": "Flag not found"})
        return JSONResponse(status_code=200, content=result.to_dict())

    @app.post("/admin/flags/{flag_name}/rollback")
    async def rollback_flag(flag_name: str, request: Request) -> JSONResponse:
        auth_err = _check_admin_auth(request, required_key)
        if auth_err is not None:
            return auth_err
        actor = request.headers.get("x-actor", "admin-ui")
        try:
            body = await request.json()
        except Exception:
            body = {}
        reason = str(body.get("reason", "Manual rollback via Admin UI"))
        try:
            payload = await controller.rollback(flag_name, reason=reason, actor=actor)
        except KeyError:
            return JSONResponse(status_code=404, content={"error": "Flag not found"})
        return JSONResponse(status_code=200, content=payload)

    @app.get("/admin/audit")
    async def get_audit_log(request: Request, limit: int = 100) -> JSONResponse:
        auth_err = _check_admin_auth(request, required_key)
        if auth_err is not None:
            return auth_err
        return JSONResponse(
            status_code=200,
            content={"audit_log": [e.to_dict() for e in service.get_audit_log(limit=limit)]},
        )

    return app


app = create_admin_app()
