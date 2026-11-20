"""
Integration tests for EVO-015: Progressive Rollout, Admin UI, Orchestrator Gating & CLI.
"""

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from product_value_system.cli.main import main as cli_main
from product_value_system.feature_flags.admin_ui import create_admin_app
from product_value_system.feature_flags.models import FeatureFlag, FlagType, RolloutStage
from product_value_system.feature_flags.rollout import MetricsSLOMonitor, RolloutController
from product_value_system.feature_flags.service import FeatureFlagService
from product_value_system.feature_flags.storage import FlagStorage, load_flags
from product_value_system.monitoring.metrics import PVSMetrics
from product_value_system.orchestrator.checkpoints import CheckpointManager
from product_value_system.orchestrator.controller import (
    OrchestratorError,
    ValueStreamOrchestrator,
)


def _write_sample_flags(tmp_path: Path) -> Path:
    flags_path = tmp_path / "flags.yaml"
    FlagStorage(flags_path).save(
        {
            "test_flag": FeatureFlag(
                name="test_flag",
                description="Integration rollout test flag",
                type=FlagType.RELEASE,
                enabled=True,
                rollout_stage=RolloutStage.DARK,
                rollout_percentage=0.0,
            ),
            "multi_agent_review": FeatureFlag(
                name="multi_agent_review",
                description="Parallel 4-agent review",
                type=FlagType.RELEASE,
                enabled=True,
                rollout_stage=RolloutStage.DARK,
                rollout_percentage=0.0,
                targeting_rules=[{"type": "environment", "operator": "equals", "value": "staging"}],
            ),
            "cost_kill_switch": FeatureFlag(
                name="cost_kill_switch",
                description="Emergency kill switch",
                type=FlagType.OPS,
                enabled=False,
                rollout_stage=RolloutStage.FULL,
                rollout_percentage=1.0,
            ),
        }
    )
    return flags_path


@pytest.mark.integration
@pytest.mark.asyncio
async def test_progressive_rollout_dark_to_full_with_pvs_metrics(tmp_path: Path) -> None:
    """Full rollout dark -> canary -> staged_5 -> staged_25 -> staged_50 -> full."""
    flags_path = _write_sample_flags(tmp_path)
    service = FeatureFlagService.from_yaml(flags_path)

    metrics = PVSMetrics(register_global_prometheus=False)
    for _ in range(100):
        metrics.record_request(agent="discovery", status="success", latency_seconds=0.3)
    for _ in range(20):
        metrics.record_evaluation(artifact_type="specification", score=0.97, passed=True)

    monitor = MetricsSLOMonitor(metrics=metrics)
    controller = RolloutController(service, monitor, auto_rollback_enabled=True)

    visited_stages: list[RolloutStage] = [service.flags["test_flag"].rollout_stage]
    while service.flags["test_flag"].rollout_stage != RolloutStage.FULL:
        res = await controller.advance_rollout("test_flag")
        assert res.advanced is True
        visited_stages.append(service.flags["test_flag"].rollout_stage)

    assert visited_stages == [
        RolloutStage.DARK,
        RolloutStage.CANARY,
        RolloutStage.STAGED_5,
        RolloutStage.STAGED_25,
        RolloutStage.STAGED_50,
        RolloutStage.FULL,
    ]
    assert service.flags["test_flag"].rollout_percentage == pytest.approx(1.0)

    # Already at full -> graceful no-op
    noop = await controller.advance_rollout("test_flag")
    assert noop.advanced is False

    # Persisted to disk
    reloaded = load_flags(flags_path)
    assert reloaded["test_flag"].rollout_stage == RolloutStage.FULL
    assert reloaded["test_flag"].rollout_percentage == pytest.approx(1.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_pvs_metrics_slo_breach_triggers_persisted_rollback(tmp_path: Path) -> None:
    """Recording real errors on PVSMetrics triggers automatic rollback persisted to YAML."""
    flags_path = _write_sample_flags(tmp_path)
    service = FeatureFlagService.from_yaml(flags_path)
    service.set_rollout("test_flag", percentage=0.05, stage=RolloutStage.CANARY)

    metrics = PVSMetrics(register_global_prometheus=False)
    for _ in range(95):
        metrics.record_request(agent="discovery", status="success")
    for _ in range(5):
        metrics.record_request(agent="discovery", status="error", error_type="TimeoutError")

    monitor = MetricsSLOMonitor(metrics=metrics)
    controller = RolloutController(service, monitor, auto_rollback_enabled=True)

    res = await controller.advance_rollout("test_flag")

    assert res.advanced is False
    assert res.rolled_back is True
    assert service.flags["test_flag"].enabled is False
    assert load_flags(flags_path)["test_flag"].enabled is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_ui_dashboard_detail_and_toggle(tmp_path: Path) -> None:
    """Admin UI renders HTML dashboard, detail page, and toggles flags."""
    flags_path = _write_sample_flags(tmp_path)
    service = FeatureFlagService.from_yaml(flags_path)
    admin_app = create_admin_app(flag_service=service, api_key="")

    async with AsyncClient(
        transport=ASGITransport(app=admin_app),
        base_url="http://test",
    ) as client:
        health = await client.get("/health")
        assert health.status_code == 200

        dash = await client.get("/admin/flags")
        assert dash.status_code == 200
        assert "test_flag" in dash.text
        assert "multi_agent_review" in dash.text

        detail = await client.get("/admin/flags/test_flag")
        assert detail.status_code == 200
        assert "Integration rollout test flag" in detail.text

        missing = await client.get("/admin/flags/nonexistent")
        assert missing.status_code == 404

        before = service.flags["test_flag"].enabled
        toggle_resp = await client.post("/admin/flags/test_flag/toggle", headers={"X-Actor": "ops"})
        assert toggle_resp.status_code == 200
        assert toggle_resp.json()["enabled"] is (not before)
        assert load_flags(flags_path)["test_flag"].enabled is (not before)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_ui_rollout_advance_rollback_and_api_key_auth(tmp_path: Path) -> None:
    """Admin UI updates rollout %, advances stage, rolls back, and enforces API key auth."""
    flags_path = _write_sample_flags(tmp_path)
    service = FeatureFlagService.from_yaml(flags_path)
    metrics = PVSMetrics(register_global_prometheus=False)
    metrics.record_request(agent="discovery", status="success")
    controller = RolloutController(service, MetricsSLOMonitor(metrics=metrics))
    admin_app = create_admin_app(
        flag_service=service,
        rollout_controller=controller,
        api_key="admin-secret-token",
    )

    async with AsyncClient(
        transport=ASGITransport(app=admin_app),
        base_url="http://test",
    ) as client:
        # Unauthorized mutation blocked with 401
        unauth = await client.post("/admin/flags/test_flag/toggle")
        assert unauth.status_code == 401

        # Rollout update with X-API-Key
        rollout_resp = await client.post(
            "/admin/flags/test_flag/rollout",
            json={"percentage": 0.25, "stage": "staged_25"},
            headers={"X-API-Key": "admin-secret-token"},
        )
        assert rollout_resp.status_code == 200
        assert rollout_resp.json()["rollout_percentage"] == pytest.approx(0.25)
        assert rollout_resp.json()["rollout_stage"] == "staged_25"

        # Advance with Bearer token -> staged_50
        adv_resp = await client.post(
            "/admin/flags/test_flag/advance",
            headers={"Authorization": "Bearer admin-secret-token"},
        )
        assert adv_resp.status_code == 200
        assert adv_resp.json()["to_stage"] == "staged_50"

        # Emergency rollback via Admin UI
        rb_resp = await client.post(
            "/admin/flags/test_flag/rollback",
            json={"reason": "Operator emergency stop"},
            headers={"X-API-Key": "admin-secret-token"},
        )
        assert rb_resp.status_code == 200
        assert rb_resp.json()["enabled"] is False
        assert service.flags["test_flag"].enabled is False

        # Audit trail captures all 3 mutations
        audit_resp = await client.get(
            "/admin/audit",
            headers={"X-API-Key": "admin-secret-token"},
        )
        assert audit_resp.status_code == 200
        actions = [e["action"] for e in audit_resp.json()["audit_log"]]
        assert actions == ["update_rollout", "advance", "rollback"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_orchestrator_blocks_run_when_cost_kill_switch_enabled(tmp_path: Path) -> None:
    """Enabling ops flag cost_kill_switch immediately aborts ValueStreamOrchestrator runs."""
    flags_path = _write_sample_flags(tmp_path)
    service = FeatureFlagService.from_yaml(flags_path)
    service.toggle_flag("cost_kill_switch", actor="incident-commander")

    orchestrator = ValueStreamOrchestrator(
        agents={},
        evaluation_pipeline=None,
        policy_engine=None,
        checkpoint_manager=CheckpointManager(tmp_path / "checkpoints"),
        artifact_root=tmp_path / "artifacts",
        feature_flag_service=service,
    )

    with pytest.raises(OrchestratorError, match="cost_kill_switch"):
        await orchestrator.execute_run(
            problem={"id": "prob-kill-test", "title": "Kill switch test"},
            config={"user_id": "user-123"},
        )

    assert any(ev["type"] == "feature_flag.evaluated" for ev in orchestrator.events)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_orchestrator_enables_multi_agent_review_via_feature_flag(tmp_path: Path) -> None:
    """multi_agent_review flag targeting staging activates multi-agent review in orchestrator."""
    flags_path = _write_sample_flags(tmp_path)
    service = FeatureFlagService.from_yaml(flags_path)

    class _StubAgent:
        def __init__(self, name: str, payload: dict[str, Any]) -> None:
            self.name = name
            self._payload = payload

        async def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return dict(self._payload)

    class _StubReviewResult:
        review_id = "rev-flag-001"
        verdict = "APPROVE"
        gate_decision = "PASS"
        gate_reason = "Zero blocking findings"
        summary = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        conflicts: list[Any] = []
        total_cost_usd = 0.01
        reviewers_executed = ["security", "performance", "maintainability", "architecture"]

        def to_dict(self) -> dict[str, Any]:
            return {
                "review_id": self.review_id,
                "verdict": self.verdict,
                "gate_decision": self.gate_decision,
            }

    class _StubReviewOrchestrator:
        def __init__(self) -> None:
            self.called = False

        async def execute(self, *_args: Any, **_kwargs: Any) -> _StubReviewResult:
            self.called = True
            return _StubReviewResult()

    rev_orch = _StubReviewOrchestrator()
    orchestrator = ValueStreamOrchestrator(
        agents={
            "discovery": _StubAgent("discovery", {"id": "outcome-1", "title": "Outcome"}),
            "specification": _StubAgent("specification", {"id": "spec-1", "title": "Spec"}),
            "build": _StubAgent("build", {"id": "build-1", "status": "ok"}),
        },
        evaluation_pipeline=None,
        policy_engine=None,
        checkpoint_manager=CheckpointManager(tmp_path / "checkpoints"),
        artifact_root=tmp_path / "artifacts",
        review_orchestrator=rev_orch,
        feature_flag_service=service,
    )

    run = await orchestrator.execute_run(
        problem={"id": "prob-flag-review", "title": "Staging multi-review"},
        config={"user_id": "user-999", "environment": "staging"},
    )

    assert rev_orch.called is True
    arch_stage = run.get_stage("review_architecture")
    assert arch_stage is not None
    assert arch_stage.agent_name == "multi-agent-review-orchestrator"
    evaluated_flags = [
        ev["data"]["flag_name"]
        for ev in orchestrator.events
        if ev["type"] == "feature_flag.evaluated"
    ]
    assert "cost_kill_switch" in evaluated_flags
    assert "multi_agent_review" in evaluated_flags


@pytest.mark.integration
def test_cli_flags_list_toggle_rollout_and_evaluate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI `pvs flags` subcommands list, toggle, update rollout, and evaluate flags."""
    flags_path = _write_sample_flags(tmp_path)

    assert cli_main(["flags", "list", "--file", str(flags_path)]) == 0
    out_list = capsys.readouterr().out
    assert "test_flag" in out_list
    assert "cost_kill_switch" in out_list

    assert cli_main(["flags", "toggle", "cost_kill_switch", "--file", str(flags_path)]) == 0
    assert load_flags(flags_path)["cost_kill_switch"].enabled is True

    assert (
        cli_main(
            [
                "flags",
                "rollout",
                "test_flag",
                "--stage",
                "staged_25",
                "--percentage",
                "0.25",
                "--file",
                str(flags_path),
            ]
        )
        == 0
    )
    assert load_flags(flags_path)["test_flag"].rollout_stage == RolloutStage.STAGED_25
    assert load_flags(flags_path)["test_flag"].rollout_percentage == pytest.approx(0.25)

    assert (
        cli_main(
            [
                "flags",
                "evaluate",
                "multi_agent_review",
                "--env",
                "staging",
                "--file",
                str(flags_path),
            ]
        )
        == 0
    )
    out_eval = capsys.readouterr().out
    assert "ENABLED" in out_eval
