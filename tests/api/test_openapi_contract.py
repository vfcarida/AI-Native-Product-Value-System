"""
OpenAPI 3.x Contract Verification, Negative/Fuzz Boundary Testing, and
Real-Infra CI + Mutation Testing Configuration Audit (EVO-P3-19).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from starlette.testclient import TestClient

from product_value_system.api.app_factory import ProductionSettings, create_production_app
from product_value_system.api.auth import DEFAULT_ROLE_PERMISSIONS, TenantContext

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_API_KEY = "test-openapi-contract-key"


def _collect_refs(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str):
            out.add(node["$ref"])
        for val in node.values():
            _collect_refs(val, out)
    elif isinstance(node, list):
        for item in node:
            _collect_refs(item, out)


def test_production_asgi_openapi_contract_schema_and_routes(tmp_path: Path) -> None:
    """Production ASGI app exports a valid OpenAPI 3.x contract for all routes."""
    settings = ProductionSettings(
        database_url=f"sqlite:///{tmp_path / 'openapi.db'}",
        enable_devx=True,
        enable_audit=True,
        enable_tracing=False,
        enable_control_plane=True,
        enable_protocols=True,
        enable_integrations=True,
        github_webhook_secret="contract-gh-secret",
        slack_signing_secret="contract-slack-secret",
        api_keys={
            CONTRACT_API_KEY: TenantContext(
                tenant_id="tenant-contract",
                user_id="contract-tester",
                role="admin",
                permissions=DEFAULT_ROLE_PERMISSIONS["admin"],
            )
        },
    )
    app = create_production_app(settings)
    schema = app.openapi()

    assert str(schema.get("openapi", "")).startswith("3.")
    assert "info" in schema and "paths" in schema
    paths = schema["paths"]

    required_routes: dict[str, set[str]] = {
        "/health/live": {"get"},
        "/health/ready": {"get"},
        "/health/startup": {"get"},
        "/health/metrics-summary": {"get"},
        "/metrics": {"get"},
        "/devx": {"get"},
        "/api/v1/runs": {"get", "post"},
        "/api/v1/runs/{run_id}": {"get"},
        "/api/v1/runs/{run_id}/events": {"get"},
        "/api/v1/runs/{run_id}/approve": {"post"},
        "/api/v1/runs/{run_id}/reject": {"post"},
        "/api/v1/agents": {"get"},
        "/api/v1/mcp": {"post"},
        "/.well-known/agent.json": {"get"},
        "/.well-known/oauth-protected-resource": {"get"},
        "/api/v1/a2a/agents": {"get"},
        "/api/v1/a2a/tasks/send": {"post"},
        "/api/v1/a2a/tasks/{task_id}": {"get"},
        "/api/v1/a2a/tasks/{task_id}/events": {"get"},
        "/api/v1/webhooks/github/pr-triage": {"post"},
        "/api/v1/webhooks/slack/interactions": {"post"},
    }
    for route, methods in required_routes.items():
        assert route in paths, f"OpenAPI missing required production path {route!r}"
        declared = {m.lower() for m in paths[route]}
        assert methods <= declared, f"Path {route} missing methods {methods - declared}"

    # Verify every JSON Schema $ref resolves cleanly inside components/schemas
    refs: set[str] = set()
    _collect_refs(schema, refs)
    schemas_block = (schema.get("components") or {}).get("schemas") or {}
    for ref in refs:
        if ref.startswith("#/components/schemas/"):
            name = ref.removeprefix("#/components/schemas/")
            assert name in schemas_block, f"Broken OpenAPI $ref: {ref}"


def test_negative_fuzz_probes_never_return_unhandled_500(tmp_path: Path) -> None:
    """Malformed inputs across Control Plane, MCP, A2A, and Webhooks return clean 4xx + headers."""
    settings = ProductionSettings(
        database_url=f"sqlite:///{tmp_path / 'fuzz.db'}",
        enable_tracing=False,
        github_webhook_secret="fuzz-gh-secret",
        slack_signing_secret="fuzz-slack-secret",
        api_keys={
            CONTRACT_API_KEY: TenantContext(
                tenant_id="tenant-fuzz",
                user_id="fuzzer",
                role="admin",
                permissions=DEFAULT_ROLE_PERMISSIONS["admin"],
            )
        },
    )
    app = create_production_app(settings)

    with TestClient(app, raise_server_exceptions=False) as client:
        probes = [
            client.post(
                "/api/v1/runs",
                headers={"X-API-Key": CONTRACT_API_KEY},
                json={"problem_id": "", "title": ""},
            ),
            client.post(
                "/api/v1/runs",
                headers={"X-API-Key": CONTRACT_API_KEY, "Content-Type": "application/json"},
                content=b"{not-valid-json",
            ),
            client.get("/api/v1/runs/nonexistent-id", headers={"X-API-Key": CONTRACT_API_KEY}),
            client.post(
                "/api/v1/a2a/tasks/send",
                headers={"X-API-Key": CONTRACT_API_KEY},
                json={"agent": "discovery-agent", "message": {}},
            ),
            client.post(
                "/api/v1/webhooks/github/pr-triage",
                content=b"{}",
            ),
            client.post(
                "/api/v1/webhooks/slack/interactions",
                content=b"{}",
            ),
        ]
        for resp in probes:
            assert 400 <= resp.status_code < 500, (
                f"Expected 4xx on negative probe, got {resp.status_code}: {resp.text}"
            )
            assert resp.headers.get("x-content-type-options") == "nosniff"
            assert resp.headers.get("x-frame-options") == "DENY"


def test_ci_workflow_defines_real_postgres_pgvector_redis_and_mutmut_jobs() -> None:
    """CI workflow includes real PostgreSQL+pgvector+Redis+Alembic job and mutmut config."""
    ci_raw = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    ci_doc = yaml.safe_load(ci_raw)
    jobs = ci_doc["jobs"]

    assert "real-infra-integration" in jobs
    infra_job = jobs["real-infra-integration"]
    assert "pgvector/pgvector:pg16" in infra_job["services"]["postgres"]["image"]
    assert "redis:7" in infra_job["services"]["redis"]["image"]
    assert "alembic upgrade head" in ci_raw

    assert "mutation-testing" in jobs
    assert "mutmut" in ci_raw

    pyproject_raw = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.mutmut]" in pyproject_raw
    assert "src/product_value_system/security/" in pyproject_raw
