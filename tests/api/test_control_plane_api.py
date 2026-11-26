"""
Comprehensive tests for the Production ASGI Composition Root (EVO-P3-01),
Enterprise JWT/API-Key Auth, Multi-Tenancy, RBAC, Distributed Rate Limiting
(EVO-P3-02), and Control Plane Durable HITL Runs API (EVO-P3-05).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from product_value_system.api.app_factory import ProductionSettings, create_production_app
from product_value_system.api.auth import (
    DEFAULT_ROLE_PERMISSIONS,
    DistributedRateLimiter,
    TenantContext,
    create_hs256_jwt,
    verify_hs256_jwt,
)
from product_value_system.api.routes.audit import AuditPrincipal
from product_value_system.cli.main import create_parser
from product_value_system.persistence.run_store import SQLiteDurableRunStore

TEST_JWT_SECRET = "unit-test-hs256-signing-secret"
TENANT_A_DEV_KEY = "test-tenant-a-dev-key"
TENANT_A_APPROVER_KEY = "test-tenant-a-approver-key"
TENANT_B_ADMIN_KEY = "test-tenant-b-admin-key"


def _build_settings(
    db_path: Path,
    *,
    rate_limit_per_minute: int = 120,
) -> ProductionSettings:
    return ProductionSettings(
        database_url=f"sqlite:///{db_path}",
        jwt_secret=TEST_JWT_SECRET,
        jwt_issuer="pvs-test-issuer",
        jwt_audience="pvs-control-plane",
        enable_devx=True,
        enable_audit=True,
        enable_tracing=False,
        enable_control_plane=True,
        allow_anonymous_dev=False,
        rate_limit_per_minute=rate_limit_per_minute,
        api_keys={
            TENANT_A_DEV_KEY: TenantContext(
                tenant_id="tenant-alpha",
                user_id="alice-dev",
                role="developer",
                permissions=DEFAULT_ROLE_PERMISSIONS["developer"],
            ),
            TENANT_A_APPROVER_KEY: TenantContext(
                tenant_id="tenant-alpha",
                user_id="bob-approver",
                role="approver",
                permissions=DEFAULT_ROLE_PERMISSIONS["approver"],
            ),
            TENANT_B_ADMIN_KEY: TenantContext(
                tenant_id="tenant-beta",
                user_id="carol-admin",
                role="admin",
                permissions=DEFAULT_ROLE_PERMISSIONS["admin"],
            ),
        },
        audit_principals={
            TENANT_B_ADMIN_KEY: AuditPrincipal(
                user_id="carol-admin",
                role="admin",
                permissions=DEFAULT_ROLE_PERMISSIONS["admin"],
            ),
        },
    )


class _UnhealthyStore(SQLiteDurableRunStore):
    """Store stub that reports unhealthy DB connectivity for readiness failure tests."""

    async def health_check(self) -> bool:
        return False


class _FakeAsyncRedis:
    """Minimal async Redis ZSET implementation for distributed rate limiter testing."""

    def __init__(self) -> None:
        self._zsets: dict[str, dict[str, float]] = {}
        self.expirations: dict[str, int] = {}

    async def zremrangebyscore(self, key: str, _min: Any, max_score: float) -> int:
        bucket = self._zsets.setdefault(key, {})
        to_remove = [m for m, s in bucket.items() if s <= float(max_score)]
        for member in to_remove:
            del bucket[member]
        return len(to_remove)

    async def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        bucket = self._zsets.setdefault(key, {})
        bucket.update(mapping)
        return len(mapping)

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True


def test_lifespan_health_probes_devx_and_audit_wiring(tmp_path: Path) -> None:
    """Production ASGI app wires DB readiness, pool stats, DevX, Audit, and OWASP headers."""
    db_file = tmp_path / "control-plane.db"
    app = create_production_app(_build_settings(db_file))

    with TestClient(app) as client:
        live = client.get("/health/live")
        assert live.status_code == 200
        assert live.headers["x-content-type-options"] == "nosniff"
        assert live.headers["x-frame-options"] == "DENY"

        startup = client.get("/health/startup")
        assert startup.status_code == 200
        assert startup.json()["status"] == "started"

        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["checks"]["database"]["status"] == "ok"

        summary = client.get("/health/metrics-summary")
        assert summary.status_code == 200
        body = summary.json()
        assert body["database_pool"]["backend"] == "sqlite"
        assert body["database_pool"]["connected"] is True

        devx = client.get("/devx")
        assert devx.status_code == 200
        assert devx.json()["aggregate_only"] is True

        audit_unauth = client.get("/api/v1/audit/search")
        assert audit_unauth.status_code == 401

        audit_ok = client.get(
            "/api/v1/audit/search",
            headers={"X-API-Key": TENANT_B_ADMIN_KEY},
        )
        assert audit_ok.status_code == 200


def test_readiness_returns_503_when_database_unhealthy(tmp_path: Path) -> None:
    """Readiness probe must fail closed with HTTP 503 when DB connectivity drops."""
    db_file = tmp_path / "unhealthy.db"
    failing_store = _UnhealthyStore(db_file)
    app = create_production_app(_build_settings(db_file), run_store=failing_store)

    with TestClient(app) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json()["status"] == "not_ready"


def test_control_plane_jwt_auth_and_validation(tmp_path: Path) -> None:
    """HS256 JWT bearer auth validates signature, issuer, audience, and expiration."""
    db_file = tmp_path / "jwt.db"
    app = create_production_app(_build_settings(db_file))

    valid_token = create_hs256_jwt(
        {
            "sub": "jwt-user-1",
            "tenant_id": "tenant-jwt",
            "role": "developer",
            "iss": "pvs-test-issuer",
            "aud": "pvs-control-plane",
        },
        TEST_JWT_SECRET,
        expires_in_seconds=600,
    )
    expired_token = create_hs256_jwt(
        {
            "sub": "jwt-user-1",
            "tenant_id": "tenant-jwt",
            "role": "developer",
            "iss": "pvs-test-issuer",
            "aud": "pvs-control-plane",
        },
        TEST_JWT_SECRET,
        expires_in_seconds=-120,
    )
    wrong_secret_token = create_hs256_jwt(
        {
            "sub": "jwt-user-1",
            "tenant_id": "tenant-jwt",
            "iss": "pvs-test-issuer",
            "aud": "pvs-control-plane",
        },
        "completely-different-secret",
    )

    with pytest.raises(ValueError, match="expired"):
        verify_hs256_jwt(
            expired_token,
            TEST_JWT_SECRET,
            issuer="pvs-test-issuer",
            audience="pvs-control-plane",
        )

    with TestClient(app) as client:
        assert client.get("/api/v1/runs").status_code == 401
        assert (
            client.get(
                "/api/v1/agents",
                headers={"Authorization": f"Bearer {expired_token}"},
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/api/v1/agents",
                headers={"Authorization": f"Bearer {wrong_secret_token}"},
            ).status_code
            == 401
        )

        agents_resp = client.get(
            "/api/v1/agents",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert agents_resp.status_code == 200
        assert agents_resp.headers["x-tenant-id"] == "tenant-jwt"
        data = agents_resp.json()
        assert data["tenant_id"] == "tenant-jwt"
        assert data["count"] >= 7


def test_control_plane_multitenancy_rbac_and_hitl_approval_flow(tmp_path: Path) -> None:
    """Runs are strictly tenant-isolated, RBAC-gated, and resumable via HITL approval."""
    db_file = tmp_path / "hitl-flow.db"
    app = create_production_app(_build_settings(db_file))

    with TestClient(app) as client:
        # 1. Tenant Alpha developer starts a run requiring human approval
        create_resp = client.post(
            "/api/v1/runs",
            headers={"X-API-Key": TENANT_A_DEV_KEY},
            json={
                "problem_id": "PROB-042",
                "title": "Checkout Latency Reduction",
                "description": "Reduce p95 checkout latency below 250ms.",
                "require_human_approval": True,
            },
        )
        assert create_resp.status_code == 201
        run = create_resp.json()
        run_id = run["run_id"]
        assert run["tenant_id"] == "tenant-alpha"
        assert run["status"] == "waiting_approval"
        assert len(run["approvals"]) == 1
        assert run["approvals"][0]["status"] == "pending"

        # 2. Cross-tenant isolation: Tenant Beta cannot view, list, approve, or reject
        assert (
            client.get(
                f"/api/v1/runs/{run_id}",
                headers={"X-API-Key": TENANT_B_ADMIN_KEY},
            ).status_code
            == 404
        )
        beta_list = client.get("/api/v1/runs", headers={"X-API-Key": TENANT_B_ADMIN_KEY})
        assert beta_list.status_code == 200
        assert beta_list.json()["count"] == 0
        assert (
            client.post(
                f"/api/v1/runs/{run_id}/approve",
                headers={"X-API-Key": TENANT_B_ADMIN_KEY},
                json={"comment": "cross-tenant attempt"},
            ).status_code
            == 404
        )

        # 3. RBAC enforcement: Developer cannot approve own run (403)
        dev_approve = client.post(
            f"/api/v1/runs/{run_id}/approve",
            headers={"X-API-Key": TENANT_A_DEV_KEY},
            json={"comment": "self approve"},
        )
        assert dev_approve.status_code == 403

        # 4. Approver resolves HITL gate -> run transitions to completed
        ok_approve = client.post(
            f"/api/v1/runs/{run_id}/approve",
            headers={"X-API-Key": TENANT_A_APPROVER_KEY},
            json={"comment": "Approved after architecture review"},
        )
        assert ok_approve.status_code == 200
        completed = ok_approve.json()
        assert completed["status"] == "completed"
        assert completed["approvals"][0]["status"] == "approved"
        assert completed["approvals"][0]["decided_by"] == "bob-approver"

        # 5. Rejection flow on a second run
        second = client.post(
            "/api/v1/runs",
            headers={"X-API-Key": TENANT_A_DEV_KEY},
            json={
                "problem_id": "PROB-043",
                "title": "Risky Migration",
                "require_human_approval": True,
            },
        ).json()
        rej = client.post(
            f"/api/v1/runs/{second['run_id']}/reject",
            headers={"X-API-Key": TENANT_A_APPROVER_KEY},
            json={"comment": "Insufficient rollback plan"},
        )
        assert rej.status_code == 200
        assert rej.json()["status"] == "cancelled"
        assert rej.json()["approvals"][0]["status"] == "rejected"


def test_rate_limiting_local_and_redis_backend(tmp_path: Path) -> None:
    """Rate limiter enforces HTTP 429 + Retry-After on both local and Redis backends."""
    db_file = tmp_path / "ratelimit.db"
    fake_redis = _FakeAsyncRedis()
    app = create_production_app(
        _build_settings(db_file, rate_limit_per_minute=2),
        redis_client=fake_redis,
    )

    with TestClient(app) as client:
        r1 = client.get("/api/v1/agents", headers={"X-API-Key": TENANT_A_DEV_KEY})
        r2 = client.get("/api/v1/agents", headers={"X-API-Key": TENANT_A_DEV_KEY})
        r3 = client.get("/api/v1/agents", headers={"X-API-Key": TENANT_A_DEV_KEY})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429
        assert int(r3.headers["retry-after"]) >= 1
        assert fake_redis.expirations


@pytest.mark.asyncio
async def test_distributed_rate_limiter_falls_back_cleanly_on_redis_error() -> None:
    """If Redis raises an error, DistributedRateLimiter degrades gracefully to local bucket."""

    class _BrokenRedis:
        async def zremrangebyscore(self, *_args: Any) -> int:
            raise ConnectionError("redis unreachable")

    limiter = DistributedRateLimiter(
        max_requests=1,
        window_seconds=60.0,
        redis_client=_BrokenRedis(),
    )
    allowed_1, _ = await limiter.check_and_record("tenant-x:u1")
    allowed_2, retry_after = await limiter.check_and_record("tenant-x:u1")
    assert allowed_1 is True
    assert allowed_2 is False
    assert retry_after >= 1


def test_cli_serve_subcommand_parses_options() -> None:
    """``pvs serve`` subcommand exposes host, port, workers, and reload arguments."""
    parser = create_parser()
    args = parser.parse_args(
        ["serve", "--host", "127.0.0.1", "--port", "9000", "--workers", "2", "--reload"]
    )
    assert args.command == "serve"
    assert args.host == "127.0.0.1"
    assert args.port == 9000
    assert args.workers == 2
    assert args.reload is True
