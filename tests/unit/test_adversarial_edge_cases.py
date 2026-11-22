"""Adversarial input, NaN/Inf bypass, overflow, and async concurrency tests (Round 5)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from product_value_system.caching.prompt_cache import MemoryCacheBackend, PromptCache
from product_value_system.harness.resilience import (
    CircuitBreaker,
    RetryTokenBucket,
    full_jitter_delay,
)
from product_value_system.policy.engine import Decision, PolicyEngine
from product_value_system.policy.entities import (
    Agent,
    AgentRole,
    Classification,
    RiskLevel,
    Tool,
    TrustLevel,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_cost",
    [float("nan"), float("-inf"), float("inf"), -0.01, "NaN", "not-a-number"],
)
def test_policy_engine_fails_closed_on_adversarial_cost_values(bad_cost: object) -> None:
    """IEEE 754 NaN/Inf or negative/malformed cost_so_far must NEVER bypass budget forbid."""
    engine = PolicyEngine(
        REPO_ROOT / "policies" / "agent_authorization.cedar",
        REPO_ROOT / "policies" / "schema.cedarschema",
    )
    agent = Agent(
        id="disc-adv",
        role=AgentRole(name="discovery", trust_level=TrustLevel.MEDIUM),
        version="0.1.0",
        cost_budget=Decimal("10.0"),
        allowed_environments={"sandbox"},
    )
    kb = Tool(
        name="knowledge_base",
        classification=Classification.READ,
        risk_level=RiskLevel.LOW,
        data_access={"filesystem"},
        requires_approval=False,
    )
    decision = engine.authorize(
        principal=agent,
        action="query",
        resource=kb,
        context={
            "environment": "sandbox",
            "has_human_approval": False,
            "cost_so_far": bad_cost,
            "iteration_count": 0,
            "stage": "discovery",
        },
    )
    assert decision.decision == Decision.DENY


@pytest.mark.unit
def test_policy_engine_fails_closed_on_negative_iteration_count() -> None:
    """Negative iteration_count must fail closed instead of bypassing iteration limits."""
    engine = PolicyEngine(
        REPO_ROOT / "policies" / "agent_authorization.cedar",
        REPO_ROOT / "policies" / "schema.cedarschema",
    )
    build_agent = Agent(
        id="build-adv",
        role=AgentRole(name="build", trust_level=TrustLevel.HIGH),
        version="0.1.0",
        cost_budget=Decimal("50.0"),
        allowed_environments={"sandbox"},
    )
    decision = engine.authorize(
        principal=build_agent,
        action="execute",
        resource="sandbox",
        context={
            "environment": "sandbox",
            "has_human_approval": False,
            "cost_so_far": 1.0,
            "iteration_count": -1,
            "stage": "build",
        },
    )
    assert decision.decision == Decision.DENY
    assert "policy_forbid_invalid_context" in decision.policies_matched


@pytest.mark.unit
def test_resilience_prevents_overflow_and_rejects_non_finite_parameters() -> None:
    """Huge attempt counts must not OverflowError; NaN/Inf must raise ValueError immediately."""
    delay = full_jitter_delay(5000, base_delay_seconds=0.5, max_delay_seconds=30.0)
    assert 0.0 <= delay <= 30.0

    with pytest.raises(ValueError, match="finite"):
        full_jitter_delay(1, base_delay_seconds=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        full_jitter_delay(1, max_delay_seconds=float("inf"))
    with pytest.raises(ValueError, match="finite"):
        RetryTokenBucket(max_tokens=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        CircuitBreaker("adv", recovery_timeout_seconds=float("nan"))


@pytest.mark.unit
@pytest.mark.parametrize("bad_temp", [float("nan"), float("inf"), float("-inf")])
def test_prompt_cache_rejects_non_finite_temperature(bad_temp: float) -> None:
    """Non-finite temperature values must fail fast rather than emit invalid NaN JSON."""
    cache = PromptCache(backend=MemoryCacheBackend())
    with pytest.raises(ValueError, match="finite"):
        cache.compute_cache_key([{"role": "user", "content": "hi"}], "gpt-4o", bad_temp, 100)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prompt_cache_handles_concurrent_async_load() -> None:
    """40 concurrent coroutines reading/writing PromptCache maintain consistent stats."""
    cache = PromptCache(backend=MemoryCacheBackend(), enable_semantic_matching=False)
    msg = [{"role": "user", "content": "concurrent key"}]
    await cache.set(msg, "gpt-4o", 0.0, 128, {"content": "cached", "usage": {"total_tokens": 10}})

    async def reader(i: int) -> dict[str, object] | None:
        if i % 4 == 0:
            await cache.set(
                [{"role": "user", "content": f"writer-{i}"}],
                "gpt-4o",
                0.0,
                128,
                {"content": f"val-{i}", "usage": {"total_tokens": 5}},
            )
        return await cache.get(msg, "gpt-4o", 0.0, 128)

    results = await asyncio.gather(*(reader(i) for i in range(40)))
    assert all(r is not None and r["content"] == "cached" for r in results)
    assert cache.hits == 40
    assert cache.tokens_saved == 400


@pytest.mark.unit
def test_statistical_validator_and_power_analyzer_reject_non_finite_and_hold_invariants() -> None:
    """Reject NaN/Inf baselines and verify analytical power(N, MDE(N)) ~= target_power."""
    from datetime import UTC, datetime

    from product_value_system.validation.baseline import BaselineData
    from product_value_system.validation.power_analyzer import PowerAnalyzer
    from product_value_system.validation.statistical_validator import StatisticalValidator

    now = datetime.now(UTC)
    nan_baseline = BaselineData(
        metric_name="latency_ms",
        values=[100.0, 102.0, float("nan"), 98.0] * 10,
        timestamps=[now] * 40,
    )
    res = StatisticalValidator().validate(nan_baseline)
    assert not res.valid
    assert any("non-finite" in err.lower() for err in res.errors)

    analyzer = PowerAnalyzer(target_power=0.80, alpha=0.05)
    with pytest.raises(ValueError, match="non-finite"):
        analyzer.analyze(nan_baseline)

    clean_baseline = BaselineData(
        metric_name="latency_ms",
        values=[100.0 + (i % 5) for i in range(100)],
        timestamps=[now] * 100,
    )
    with pytest.raises(ValueError, match="finite"):
        analyzer.analyze(clean_baseline, effect_size=float("nan"))

    # First-principles mathematical invariant: power at the exact computed MDE ~= target_power
    pa = analyzer.analyze(clean_baseline)
    at_mde = analyzer.analyze(clean_baseline, effect_size=pa.minimum_detectable_effect)
    assert 0.77 <= at_mde.current_power <= 0.83
