"""Repository-wide AST & HTTP security posture audit suite (Round 11).

Validates OWASP Top 10 for LLM/Agentic Applications & FastAPI hardening controls:
- Zero `shell=True` subprocess invocations in `src/`
- Zero unsafe `yaml.load(...)` without `SafeLoader`
- Zero `eval()` / `exec()` / `pickle.load` calls in `src/`
- Constant-time `hmac.compare_digest` auth in monitoring & admin HTTP endpoints
- OWASP security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Cache-Control`,
  `Referrer-Policy`, `Content-Security-Policy`) enforced on all HTTP responses.
"""

from __future__ import annotations

import ast
from pathlib import Path

import httpx

from product_value_system.api.health import create_app
from product_value_system.feature_flags.admin_ui import create_admin_app
from product_value_system.feature_flags.service import FeatureFlagService
from product_value_system.monitoring.metrics import PVSMetrics

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src" / "product_value_system"


def test_ast_zero_unsafe_subprocess_yaml_eval_or_pickle_in_src() -> None:
    """Every module in src/ must be free of shell=True, unsafe yaml.load, eval/exec, pickle."""
    violations: list[str] = []

    for py_file in sorted(SRC_DIR.rglob("*.py")):
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # 1. Disallow eval() / exec() builtins
            if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
                violations.append(f"{rel}:{node.lineno}: forbidden builtin {node.func.id}()")

            # 2. Disallow subprocess(..., shell=True)
            for kw in node.keywords:
                if (
                    kw.arg == "shell"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    violations.append(f"{rel}:{node.lineno}: subprocess called with shell=True")

            # 3. Disallow yaml.load() (must use yaml.safe_load) & pickle.load/loads
            if isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                owner = node.func.value.id if isinstance(node.func.value, ast.Name) else ""
                if owner == "yaml" and attr == "load":
                    violations.append(f"{rel}:{node.lineno}: unsafe yaml.load() call")
                if owner == "pickle" and attr in {"load", "loads"}:
                    violations.append(f"{rel}:{node.lineno}: unsafe pickle.{attr}() call")

    assert violations == [], "Security AST violations found:\n" + "\n".join(violations)


def test_http_auth_endpoints_use_constant_time_compare_digest() -> None:
    """Verify both health and admin FastAPI apps use hmac.compare_digest for API keys."""
    for rel_path in (
        "src/product_value_system/api/health.py",
        "src/product_value_system/feature_flags/admin_ui.py",
    ):
        text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        assert "hmac.compare_digest" in text, f"{rel_path} must use hmac.compare_digest"


async def test_health_and_admin_apps_enforce_security_headers_and_auth() -> None:
    """Verify OWASP security headers and API-key protection on health & admin endpoints."""
    secret_key = "test-guard-token-value"  # Synthetic test key for unit auth test
    pvs_metrics = PVSMetrics(register_global_prometheus=False)
    health_app = create_app(metrics=pvs_metrics, metrics_api_key=secret_key)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=health_app), base_url="http://test"
    ) as client:
        unauth = await client.get("/metrics")
        assert unauth.status_code == 401
        assert unauth.headers["x-content-type-options"] == "nosniff"
        assert unauth.headers["x-frame-options"] == "DENY"
        assert unauth.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in unauth.headers["content-security-policy"]

        authed = await client.get("/metrics", headers={"Authorization": f"Bearer {secret_key}"})
        assert authed.status_code == 200
        assert authed.headers["referrer-policy"] == "no-referrer"

    admin_app = create_admin_app(
        flag_service=FeatureFlagService(flags={}),
        api_key=secret_key,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=admin_app), base_url="http://test"
    ) as client:
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.headers["x-content-type-options"] == "nosniff"
        assert health.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in health.headers["content-security-policy"]

        denied = await client.post("/admin/flags/missing/toggle")
        assert denied.status_code == 401
