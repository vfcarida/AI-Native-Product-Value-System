"""
Unit tests for EVO-015: Feature Flags, Consistent Hashing, YAML Schema & Rollout Controller.
"""

import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from product_value_system.feature_flags.models import (
    FeatureFlag,
    FlagType,
    RolloutPlan,
    RolloutStage,
)
from product_value_system.feature_flags.rollout import RolloutController
from product_value_system.feature_flags.service import FeatureFlagService
from product_value_system.feature_flags.storage import (
    FlagConfigError,
    FlagStorage,
    load_flags,
)
from product_value_system.security.secret_detection import SecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]


class _StubSLOMonitor:
    """Deterministic SLO monitor stub for unit-testing quality gates and rollback."""

    def __init__(
        self,
        *,
        error_rate: float = 0.0,
        quality_score: float = 0.98,
        crash_rate: float = 0.0,
        critical_alert: str | None = None,
    ) -> None:
        self.error_rate = error_rate
        self.quality_score = quality_score
        self.crash_rate = crash_rate
        self.critical_alert = critical_alert

    async def get_error_rate(self, flag_name: str) -> float:  # noqa: ARG002
        return self.error_rate

    async def get_quality_score(self, flag_name: str) -> float:  # noqa: ARG002
        return self.quality_score

    async def get_crash_rate(self, flag_name: str) -> float:  # noqa: ARG002
        return self.crash_rate

    async def has_critical_alert(self, flag_name: str) -> str | None:  # noqa: ARG002
        return self.critical_alert


@pytest.mark.unit
def test_flag_evaluation_enabled_full_rollout() -> None:
    """Enabled flag at 100% evaluates to True with rollout percentage reason."""
    flag = FeatureFlag(
        name="test_flag",
        description="Full release feature",
        type=FlagType.RELEASE,
        enabled=True,
        rollout_percentage=1.0,
        rollout_stage=RolloutStage.FULL,
    )
    service = FeatureFlagService({"test_flag": flag})
    evaluation = service.is_enabled("test_flag", user_id="user-123")

    assert evaluation.enabled is True
    assert "rollout percentage" in evaluation.reason.lower()
    assert evaluation.user_id == "user-123"


@pytest.mark.unit
def test_flag_evaluation_master_switch_off_and_unknown_flag() -> None:
    """Disabled master switch and unknown flags both fail closed."""
    flag = FeatureFlag(
        name="disabled_flag",
        enabled=False,
        rollout_percentage=1.0,
        rollout_stage=RolloutStage.FULL,
        targeting_rules=[{"type": "user_id", "operator": "in", "values": ["user-123"]}],
    )
    service = FeatureFlagService({"disabled_flag": flag})

    disabled_eval = service.is_enabled("disabled_flag", user_id="user-123")
    assert disabled_eval.enabled is False
    assert "master switch" in disabled_eval.reason.lower()

    missing_eval = service.is_enabled("does_not_exist", user_id="user-123")
    assert missing_eval.enabled is False
    assert "not found" in missing_eval.reason.lower()


@pytest.mark.unit
def test_targeting_rules_user_environment_and_attribute() -> None:
    """Verify user_id, environment, and attribute targeting rules."""
    flag = FeatureFlag(
        name="targeted_flag",
        enabled=True,
        rollout_percentage=0.0,
        rollout_stage=RolloutStage.DARK,
        targeting_rules=[
            {"type": "user_id", "operator": "in", "values": ["beta-1", "beta-2"]},
            {"type": "environment", "operator": "equals", "value": "staging"},
            {"type": "attribute", "attribute": "plan", "operator": "equals", "value": "enterprise"},
        ],
    )
    service = FeatureFlagService({"targeted_flag": flag})

    assert service.is_enabled("targeted_flag", user_id="beta-1").enabled is True
    assert (
        service.is_enabled(
            "targeted_flag",
            user_id="regular-user",
            request_context={"environment": "staging"},
        ).enabled
        is True
    )
    assert (
        service.is_enabled(
            "targeted_flag",
            user_id="regular-user",
            request_context={"environment": "production", "plan": "enterprise"},
        ).enabled
        is True
    )
    assert (
        service.is_enabled(
            "targeted_flag",
            user_id="regular-user",
            request_context={"environment": "production", "plan": "free"},
        ).enabled
        is False
    )


@pytest.mark.unit
def test_consistent_hashing_determinism_and_distribution() -> None:
    """Consistent SHA-256 hashing is deterministic per user and uniform across users."""
    service = FeatureFlagService({})

    # Same user always maps identically across repeated evaluations
    first = service._is_in_rollout_percentage("test_flag", "user-123", 0.50)
    for _ in range(50):
        assert service._is_in_rollout_percentage("test_flag", "user-123", 0.50) is first

    # Statistical distribution across 1,000 distinct users for 25% rollout
    hits = sum(
        1 for idx in range(1000) if service._is_in_rollout_percentage("exp_flag", f"u-{idx}", 0.25)
    )
    assert 200 <= hits <= 300


@pytest.mark.unit
def test_flag_evaluation_latency_under_1ms() -> None:
    """Verify in-memory flag evaluation runs well under the 1ms performance budget."""
    flag = FeatureFlag(
        name="perf_flag",
        enabled=True,
        rollout_percentage=0.25,
        rollout_stage=RolloutStage.STAGED_25,
        targeting_rules=[{"type": "user_id", "operator": "in", "values": ["vip-1"]}],
    )
    service = FeatureFlagService({"perf_flag": flag})

    start = time.perf_counter()
    for idx in range(1000):
        service.is_enabled("perf_flag", user_id=f"user-{idx}", request_context={"request_id": "r1"})
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    avg_ms = elapsed_ms / 1000.0
    assert avg_ms < 1.0, f"Flag evaluation averaged {avg_ms:.4f}ms (>1ms target)"


@pytest.mark.unit
def test_yaml_storage_load_save_roundtrip_and_schema_validation(tmp_path: Path) -> None:
    """FlagStorage round-trips flags cleanly and rejects schema-invalid payloads."""
    flags_path = tmp_path / "flags.yaml"
    storage = FlagStorage(flags_path)
    original = {
        "feature_alpha": FeatureFlag(
            name="feature_alpha",
            description="Alpha rollout",
            type=FlagType.RELEASE,
            enabled=True,
            rollout_stage=RolloutStage.CANARY,
            rollout_percentage=0.05,
            targeting_rules=[{"type": "user_id", "operator": "in", "values": ["u-1"]}],
        )
    }
    storage.save(original)
    loaded = storage.load()

    assert set(loaded.keys()) == {"feature_alpha"}
    assert loaded["feature_alpha"].rollout_stage == RolloutStage.CANARY
    assert loaded["feature_alpha"].rollout_percentage == pytest.approx(0.05)

    # Reject invalid flag name / out-of-range percentage / malicious extra field
    bad_path = tmp_path / "bad_flags.yaml"
    bad_path.write_text(
        yaml.safe_dump(
            {
                "flags": [
                    {
                        "name": "INVALID NAME!",
                        "type": "release",
                        "enabled": True,
                        "rollout_stage": "canary",
                        "rollout_percentage": 1.5,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FlagConfigError):
        FlagStorage(bad_path).load()


@pytest.mark.unit
def test_toggle_and_set_rollout_record_audit_log(tmp_path: Path) -> None:
    """Service mutations persist to YAML and record immutable AuditEntry items."""
    flags_path = tmp_path / "flags.yaml"
    FlagStorage(flags_path).save(
        {
            "ops_switch": FeatureFlag(
                name="ops_switch",
                type=FlagType.OPS,
                enabled=False,
                rollout_stage=RolloutStage.FULL,
                rollout_percentage=1.0,
            )
        }
    )
    service = FeatureFlagService.from_yaml(flags_path)
    service.toggle_flag("ops_switch", actor="sre-alice")
    service.set_rollout("ops_switch", percentage=0.5, stage=RolloutStage.STAGED_50, actor="sre-bob")

    reloaded = load_flags(flags_path)
    assert reloaded["ops_switch"].enabled is True
    assert reloaded["ops_switch"].rollout_percentage == pytest.approx(0.5)
    assert reloaded["ops_switch"].rollout_stage == RolloutStage.STAGED_50

    audit = service.get_audit_log()
    assert len(audit) == 2
    assert audit[0].action == "toggle" and audit[0].actor == "sre-alice"
    assert audit[1].action == "update_rollout" and audit[1].actor == "sre-bob"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rollout_controller_advances_stage_from_dark_to_canary() -> None:
    """Healthy dark launch advances to canary (5%) and emits rollout_advanced event."""
    flag = FeatureFlag(
        name="test_flag",
        enabled=True,
        rollout_stage=RolloutStage.DARK,
        rollout_percentage=0.0,
    )
    service = FeatureFlagService({"test_flag": flag})
    monitor = _StubSLOMonitor(error_rate=0.0, crash_rate=0.0, quality_score=0.99)
    controller = RolloutController(service, monitor)

    result = await controller.advance_rollout("test_flag")

    assert result.advanced is True
    assert flag.rollout_stage == RolloutStage.CANARY
    assert flag.rollout_percentage == pytest.approx(0.05)
    assert any(ev["event_type"] == "rollout_advanced" for ev in controller.events)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_automatic_rollback_on_error_rate_breach() -> None:
    """Error rate >1% in canary triggers automatic rollback disabling the flag."""
    flag = FeatureFlag(
        name="canary_flag",
        enabled=True,
        rollout_stage=RolloutStage.CANARY,
        rollout_percentage=0.05,
    )
    service = FeatureFlagService({"canary_flag": flag})
    monitor = _StubSLOMonitor(error_rate=0.05, quality_score=0.98)
    alerts: list[str] = []
    controller = RolloutController(
        service,
        monitor,
        auto_rollback_enabled=True,
        alert_callback=lambda msg: alerts.append(msg),
    )

    gates_pass = await controller._check_quality_gates(flag)

    assert gates_pass is False
    assert flag.enabled is False
    assert len(alerts) == 1
    assert "canary_flag" in alerts[0]
    assert any(ev["event_type"] == "rollback_triggered" for ev in controller.events)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_automatic_rollback_on_quality_score_drop() -> None:
    """Quality score <95% in staged rollout triggers automatic rollback."""
    flag = FeatureFlag(
        name="staged_flag",
        enabled=True,
        rollout_stage=RolloutStage.STAGED_25,
        rollout_percentage=0.25,
    )
    service = FeatureFlagService({"staged_flag": flag})
    monitor = _StubSLOMonitor(error_rate=0.002, quality_score=0.88)
    controller = RolloutController(service, monitor, auto_rollback_enabled=True)

    result = await controller.advance_rollout("staged_flag")

    assert result.advanced is False
    assert result.rolled_back is True
    assert flag.enabled is False
    assert flag.rollout_stage == RolloutStage.STAGED_25


@pytest.mark.unit
@pytest.mark.asyncio
async def test_automatic_rollback_on_critical_alert() -> None:
    """Active CRITICAL alert immediately rolls back the flag even with low error rate."""
    flag = FeatureFlag(
        name="critical_flag",
        enabled=True,
        rollout_stage=RolloutStage.STAGED_50,
        rollout_percentage=0.50,
    )
    service = FeatureFlagService({"critical_flag": flag})
    monitor = _StubSLOMonitor(
        error_rate=0.0,
        quality_score=1.0,
        critical_alert="CRITICAL: SLOBurnRateFast firing on availability",
    )
    controller = RolloutController(service, monitor, auto_rollback_enabled=True)

    gates_pass = await controller._check_quality_gates(flag)

    assert gates_pass is False
    assert flag.enabled is False
    assert any("CRITICAL" in msg for msg in controller.alerts_sent)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dark_launch_quality_gate_requires_zero_errors_and_crashes() -> None:
    """Dark launch enforces zero tolerance (error_rate=0, crash_rate=0)."""
    flag = FeatureFlag(
        name="dark_flag",
        enabled=True,
        rollout_stage=RolloutStage.DARK,
        rollout_percentage=0.0,
    )
    service = FeatureFlagService({"dark_flag": flag})
    monitor = _StubSLOMonitor(error_rate=0.0, crash_rate=0.01)
    controller = RolloutController(service, monitor, auto_rollback_enabled=False)

    result = await controller.advance_rollout("dark_flag")

    assert result.advanced is False
    assert result.rolled_back is False
    assert flag.enabled is True
    assert flag.rollout_stage == RolloutStage.DARK


@pytest.mark.unit
def test_rollout_plan_serialization_covers_all_6_stages() -> None:
    """RolloutPlan serializes all 6 progressive stages and quality gates."""
    plan = RolloutPlan(flag_name="multi_agent_review")
    payload: dict[str, Any] = plan.to_dict()
    stages = [s["stage"] for s in payload["stages"]]

    assert stages == ["dark", "canary", "staged_5", "staged_25", "staged_50", "full"]
    assert payload["auto_rollback_enabled"] is True
    assert payload["quality_gates"]["canary"]["quality_score"] == pytest.approx(0.95)


@pytest.mark.unit
def test_repo_flags_yaml_and_compose_valid_with_zero_secrets() -> None:
    """Validate repo flags.yaml against schema and confirm zero secrets."""
    flags_file = REPO_ROOT / "flags.yaml"
    compose_file = REPO_ROOT / "docker-compose.monitoring.yml"
    detector = SecretDetector()

    flags = load_flags(flags_file)
    assert {"new_discovery_prompt", "multi_agent_review", "cost_kill_switch"}.issubset(
        set(flags.keys())
    )
    assert flags["cost_kill_switch"].type == FlagType.OPS
    assert flags["cost_kill_switch"].enabled is False

    for path in (flags_file, compose_file):
        raw = path.read_text(encoding="utf-8")
        assert detector.find_secrets(raw) == [], f"Secret detected in {path}"

    compose_data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    assert "pvs-flags-admin" in compose_data["services"]
