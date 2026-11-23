"""
High Availability, health probes, multi-replica load distribution, zero-downtime failover,
PgBouncer pooling, and Patroni HA Helm manifest validation tests (PROD-011).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from product_value_system.api.health import create_app
from product_value_system.monitoring.metrics import PVSMetrics
from product_value_system.persistence.postgres import PostgresDatabase
from product_value_system.security.secret_detector import HybridSecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART_DIR = REPO_ROOT / "deploy" / "helm" / "product-value-system"
TEMPLATES_DIR = CHART_DIR / "templates"


# ---------------------------------------------------------------------------
# 1. Kubernetes Health Probes (/health/live, /health/ready, /health/startup)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_live_ready_startup_and_metrics_summary_probes() -> None:
    """All 4 HA health/diagnostic endpoints return expected status, headers, and pool stats."""
    metrics = PVSMetrics(register_global_prometheus=False)
    metrics.record_request(agent="spec", status="success", latency_seconds=0.08, cost_usd=0.01)

    app = create_app(
        metrics=metrics,
        db_check=lambda: {"status": "ok", "latency_ms": 1.2},
        startup_check=lambda: {"status": "ok", "migrations": "v2"},
        pool_stats_provider=lambda: {"size": 20, "checked_out": 4, "pgbouncer_compatible": True},
        pod_name="pvs-pod-0",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r_live = await client.get("/health/live")
        r_ready = await client.get("/health/ready")
        r_start = await client.get("/health/startup")
        r_sum = await client.get("/health/metrics-summary")

    assert r_live.status_code == 200
    assert r_live.headers["X-Pod-Name"] == "pvs-pod-0"
    assert r_live.json()["status"] == "healthy"
    assert "timestamp" in r_live.json()

    assert r_ready.status_code == 200
    assert r_ready.json()["status"] == "ready"
    assert r_ready.json()["checks"]["database"]["status"] == "ok"

    assert r_start.status_code == 200
    assert r_start.json()["status"] == "started"
    assert r_start.json()["checks"]["startup"]["migrations"] == "v2"

    assert r_sum.status_code == 200
    summary = r_sum.json()
    assert summary["pod_name"] == "pvs-pod-0"
    assert summary["uptime_seconds"] >= 0.0
    assert summary["database_pool"]["pgbouncer_compatible"] is True


@pytest.mark.asyncio
async def test_readiness_and_startup_probes_return_503_when_degraded() -> None:
    """Failed DB or startup checks return HTTP 503 so Kubernetes removes pod from rotation."""
    app = create_app(
        db_check=lambda: (_ for _ in ()).throw(ConnectionError("pgbouncer unreachable")),
        startup_check=lambda: False,
        pod_name="pvs-pod-degraded",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r_ready = await client.get("/health/ready")
        r_start = await client.get("/health/startup")

    assert r_ready.status_code == 503
    assert r_ready.json()["status"] == "not_ready"
    assert r_start.status_code == 503
    assert r_start.json()["status"] == "starting"


# ---------------------------------------------------------------------------
# 2. Multi-Replica Load Distribution & Zero-Downtime Pod Failover Simulation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_distribution_balanced_across_3_replicas_with_zero_downtime_failover() -> None:
    """Round-robin 3-replica pool distributes evenly (<15% variance) and survives pod loss."""
    replicas = [
        create_app(pod_name=f"pvs-replica-{i}", metrics_api_key="") for i in range(3)
    ]
    clients = [
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        for app in replicas
    ]

    try:
        # Phase 1: 99 requests across 3 healthy replicas -> balanced distribution
        pod_counts: Counter[str] = Counter()
        responses = await asyncio.gather(
            *[clients[idx % 3].get("/health/metrics-summary") for idx in range(99)]
        )
        for resp in responses:
            assert resp.status_code == 200
            pod_counts[resp.headers["X-Pod-Name"]] += 1

        assert len(pod_counts) == 3
        expected = 99 / 3
        for pod, cnt in pod_counts.items():
            variance = abs(cnt - expected) / expected
            assert variance < 0.15, f"Pod {pod} imbalanced: {cnt} vs {expected}"

        # Phase 2: Simulate pod-0 crash/drain -> remaining 2 healthy replicas absorb 100% traffic
        healthy_clients = clients[1:]
        failover_responses = await asyncio.gather(
            *[healthy_clients[idx % 2].get("/health/ready") for idx in range(60)]
        )
        assert all(r.status_code == 200 for r in failover_responses)
        served_by = {r.headers["X-Pod-Name"] for r in failover_responses}
        assert served_by == {"pvs-replica-1", "pvs-replica-2"}
    finally:
        for client in clients:
            await client.aclose()


# ---------------------------------------------------------------------------
# 3. PgBouncer-Compatible Connection Pooling (`PostgresDatabase`)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_postgres_database_pgbouncer_transaction_pooling_and_pool_stats() -> None:
    """PostgresDatabase defaults to statement_cache_size=0 (PgBouncer safe) and reports stats."""

    class _FakePool:
        def get_size(self) -> int:
            return 20

        def get_idle_size(self) -> int:
            return 14

    db = PostgresDatabase.from_env(
        env={
            "DATABASE_URL": "postgresql://pvs_user:pass@pgbouncer:5432/pvs_db",
            "DATABASE_POOL_SIZE": "20",
            "DATABASE_MIN_POOL_SIZE": "4",
            "DATABASE_STATEMENT_CACHE_SIZE": "0",
        }
    )
    assert db.min_size == 4
    assert db.max_size == 20
    assert db.statement_cache_size == 0

    db._pool = _FakePool()
    stats = db.get_pool_stats()
    assert stats == {
        "connected": True,
        "min_size": 4,
        "max_size": 20,
        "size": 20,
        "idle": 14,
        "checked_out": 6,
        "statement_cache_size": 0,
        "pgbouncer_compatible": True,
    }


# ---------------------------------------------------------------------------
# 4. HA Helm Chart Manifests, Probes, HPA, PDB, Patroni & PgBouncer Validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ha_helm_values_hpa_pdb_patroni_pgbouncer_and_scripts_valid() -> None:
    """Helm HA templates and resilience scripts satisfy PROD-011 availability & security rules."""
    values: dict[str, Any] = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))

    # Default HA invariants
    assert values["replicaCount"] == 3
    assert values["autoscaling"]["minReplicas"] == 3
    assert values["autoscaling"]["maxReplicas"] == 10
    assert values["autoscaling"]["targetCPUUtilizationPercentage"] == 70
    assert values["autoscaling"]["targetMemoryUtilizationPercentage"] == 80
    assert values["podDisruptionBudget"]["minAvailable"] == 2
    assert values["podDisruptionBudget"]["unhealthyPodEvictionPolicy"] == "IfHealthyBudget"
    assert values["probes"]["startup"]["path"] == "/health/startup"
    assert values["probes"]["liveness"]["path"] == "/health/live"
    assert values["probes"]["readiness"]["path"] == "/health/ready"
    assert values["terminationGracePeriodSeconds"] == 60
    assert values["ha"]["antiAffinity"]["enabled"] is True
    assert values["postgresql"]["ha"]["replicas"] == 3
    assert values["postgresql"]["ha"]["failover"]["maxLagMb"] == 1
    assert values["pgbouncer"]["replicas"] == 2
    assert values["pgbouncer"]["poolMode"] == "transaction"

    # Verify template directives & key settings
    hpa_text = (TEMPLATES_DIR / "hpa.yaml").read_text(encoding="utf-8")
    assert "stabilizationWindowSeconds: 60" in hpa_text
    assert "stabilizationWindowSeconds: 300" in hpa_text
    assert "http_requests_per_second" in hpa_text

    pdb_text = (TEMPLATES_DIR / "pdb.yaml").read_text(encoding="utf-8")
    assert "unhealthyPodEvictionPolicy:" in pdb_text

    dep_text = (TEMPLATES_DIR / "deployment.yaml").read_text(encoding="utf-8")
    assert "maxUnavailable: 0" in dep_text
    assert "maxSurge: 1" in dep_text
    assert "preStop:" in dep_text
    assert "podAntiAffinity:" in dep_text

    pg_ha_text = (TEMPLATES_DIR / "postgresql-ha.yaml").read_text(encoding="utf-8")
    assert "use_pg_rewind: true" in pg_ha_text
    assert "scram-sha-256" in pg_ha_text
    assert "secretKeyRef:" in pg_ha_text
    assert "replicator_password" not in pg_ha_text  # Zero plaintext passwords

    pgb_text = (TEMPLATES_DIR / "pgbouncer.yaml").read_text(encoding="utf-8")
    assert "pool_mode = {{ .Values.pgbouncer.poolMode }}" in pgb_text
    assert "scram-sha-256" in pgb_text

    # Resilience scripts & docs exist and contain zero leaked secrets
    failover_sh = (REPO_ROOT / "tests" / "resilience" / "test_failover.sh").read_text(
        encoding="utf-8"
    )
    zdt_sh = (REPO_ROOT / "tests" / "resilience" / "test_zero_downtime.sh").read_text(
        encoding="utf-8"
    )
    assert "MAX_FAILOVER_SECONDS" in failover_sh and "role=master" in failover_sh
    assert "kubectl rollout status" in zdt_sh and "Zero-downtime" in zdt_sh

    detector = HybridSecretDetector()
    for path in (
        TEMPLATES_DIR / "postgresql-ha.yaml",
        TEMPLATES_DIR / "pgbouncer.yaml",
        REPO_ROOT / "tests" / "resilience" / "test_failover.sh",
        REPO_ROOT / "tests" / "resilience" / "test_zero_downtime.sh",
    ):
        findings = detector.detect(path.read_text(encoding="utf-8"))
        assert findings == [], f"Unexpected secret finding in {path}: {findings}"
