"""
Runnable example demonstrating EVO-015 Progressive Delivery:
1. Dark launch (0% user traffic)
2. Advance through Canary (5%) -> Staged (5% / 25% / 50%) -> Full (100%)
3. Trigger automatic SLO-based rollback on error rate breach
"""

from __future__ import annotations

import asyncio

from product_value_system.feature_flags.models import FeatureFlag, FlagType, RolloutStage
from product_value_system.feature_flags.rollout import MetricsSLOMonitor, RolloutController
from product_value_system.feature_flags.service import FeatureFlagService
from product_value_system.monitoring.metrics import PVSMetrics


async def run_progressive_rollout_demo() -> None:
    """Walk through a complete progressive delivery lifecycle + SLO auto-rollback."""
    metrics = PVSMetrics(register_global_prometheus=False)
    for _ in range(100):
        metrics.record_request(agent="discovery", status="success", latency_seconds=0.4)
    for _ in range(20):
        metrics.record_evaluation(artifact_type="outcome", score=0.97, passed=True)

    flag = FeatureFlag(
        name="demo_progressive_feature",
        description="Demo progressive delivery rollout",
        type=FlagType.RELEASE,
        enabled=True,
        rollout_stage=RolloutStage.DARK,
        rollout_percentage=0.0,
    )
    service = FeatureFlagService({"demo_progressive_feature": flag})
    monitor = MetricsSLOMonitor(metrics=metrics)
    controller = RolloutController(
        flag_service=service,
        slo_monitor=monitor,
        auto_rollback_enabled=True,
    )

    print("1. Starting progressive delivery from DARK launch:")
    while flag.rollout_stage != RolloutStage.FULL:
        res = await controller.advance_rollout(flag.name)
        print(f"   {res.from_stage.value} -> {res.to_stage.value} ({res.rollout_percentage:.0%})")

    print("\n2. Simulating production SLO breach (>1% error rate)...")
    for _ in range(5):
        metrics.record_request(agent="discovery", status="error", error_type="ProviderTimeout")

    gates_pass = await controller._check_quality_gates(flag)
    print(f"   Quality gates passed: {gates_pass}")
    print(f"   Flag enabled after auto-rollback: {flag.enabled}")
    print(f"   Alerts sent: {controller.alerts_sent}")


if __name__ == "__main__":
    asyncio.run(run_progressive_rollout_demo())
