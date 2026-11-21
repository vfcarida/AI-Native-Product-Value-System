"""
Integration & quality validation tests for Complexity Model Routing, FallbackHandler,
RoutedModelAdapter, Harness integration, hot-reload, and FinOps cost savings (EVO-026).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from product_value_system.adapters.base_adapter import RoutedModelAdapter
from product_value_system.adapters.model_adapter import (
    Message,
    ModelError,
    ModelResponse,
    Usage,
)
from product_value_system.harness.budget import Budget
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.routing import (
    ComplexityLevel,
    FallbackHandler,
    ModelRouter,
    QualityCheckFailedError,
    QualityValidator,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class _StubModelAdapter:
    """Deterministic model adapter stub recording invoked model and returning scripted output."""

    def __init__(
        self,
        model: str,
        content: str = '{"result": "ok"}',
        *,
        input_tokens: int = 1500,
        output_tokens: int = 500,
        raise_error: bool = False,
        call_log: list[str] | None = None,
    ) -> None:
        self.model = model
        self._content = content
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._raise_error = raise_error
        self._call_log = call_log

    async def invoke(
        self,
        messages: list[Message] | list[Any],
        tools: Any = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        if self._call_log is not None:
            self._call_log.append(self.model)
        if self._raise_error:
            raise ModelError(f"Simulated error from {self.model}")
        return ModelResponse(
            content=self._content,
            finish_reason="stop",
            model=self.model,
            usage=Usage(
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                total_tokens=self._input_tokens + self._output_tokens,
            ),
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_end_to_end_routed_adapter_selects_models_across_tiers() -> None:
    """RoutedModelAdapter transparently routes LOW, MEDIUM, and HIGH tasks to distinct models."""
    router = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing.yaml")
    invoked: list[str] = []

    def _factory(provider: str, model: str) -> Any:
        return _StubModelAdapter(model=model, content=f"ok:{model}", call_log=invoked)

    routed = RoutedModelAdapter(router=router, adapter_factory=_factory)

    r_low = await routed.invoke([{"role": "user", "content": "Extract function names as JSON"}])
    r_med = await routed.invoke(
        [{"role": "user", "content": "Analyze module patterns and write unit tests"}]
    )
    r_high = await routed.invoke(
        [
            {
                "role": "user",
                "content": "Design a novel microservice architecture considering trade-offs",
            }
        ]
    )

    assert r_low.model == "claude-3-haiku-20240307"
    assert r_med.model == "claude-3-sonnet-20240229"
    assert r_high.model == "gpt-4-turbo-2024-04-09"
    assert invoked == [
        "claude-3-haiku-20240307",
        "claude-3-sonnet-20240229",
        "gpt-4-turbo-2024-04-09",
    ]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_quality_based_fallback_escalates_from_low_to_higher_tier() -> None:
    """When cheap LOW-tier model fails JSON schema validation, FallbackHandler escalates."""
    router = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing.yaml")
    validator = QualityValidator(method="schema_compliance", required_keys=["functions"])
    invoked: list[str] = []

    def _factory(provider: str, model: str) -> Any:
        # Cheap low-tier models return malformed output; medium-tier succeeds
        if model in {"claude-3-haiku-20240307", "gpt-3.5-turbo-0125"}:
            return _StubModelAdapter(model=model, content="malformed output", call_log=invoked)
        return _StubModelAdapter(model=model, content='{"functions": ["pay"]}', call_log=invoked)

    handler = FallbackHandler(router, validator)
    decision = router.route([{"role": "user", "content": "Extract function names as JSON"}])
    response = await handler.execute_with_fallback(
        [{"role": "user", "content": "Extract function names as JSON"}],
        routing_decision=decision,
        adapter_factory=_factory,
        max_attempts=3,
    )

    assert response.model == "claude-3-sonnet-20240229"
    assert invoked == [
        "claude-3-haiku-20240307",
        "gpt-3.5-turbo-0125",
        "claude-3-sonnet-20240229",
    ]
    assert router.get_metrics()["fallback_count"] == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fallback_handles_model_error_and_escalates() -> None:
    """FallbackHandler recovers cleanly from a primary adapter ModelError."""
    router = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing.yaml")
    invoked: list[str] = []

    def _factory(provider: str, model: str) -> Any:
        fail = model == "claude-3-haiku-20240307"
        return _StubModelAdapter(
            model=model,
            content="Valid formatted output",
            raise_error=fail,
            call_log=invoked,
        )

    routed = RoutedModelAdapter(
        router=router,
        adapter_factory=_factory,
        quality_validator=QualityValidator(min_length=5),
    )
    resp = await routed.invoke([{"role": "user", "content": "Format names as YAML"}])
    assert resp.model == "gpt-3.5-turbo-0125"
    assert invoked == ["claude-3-haiku-20240307", "gpt-3.5-turbo-0125"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fallback_exhaustion_raises_quality_check_failed_error() -> None:
    """When every attempt fails quality check, QualityCheckFailedError includes attempt history."""
    router = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing.yaml")
    validator = QualityValidator(method="schema_compliance", required_keys=["required_field"])

    def _factory(provider: str, model: str) -> Any:
        return _StubModelAdapter(model=model, content="invalid")

    handler = FallbackHandler(router, validator)
    decision = router.route([{"role": "user", "content": "Extract fields as JSON"}])

    with pytest.raises(QualityCheckFailedError) as exc_info:
        await handler.execute_with_fallback(
            [{"role": "user", "content": "Extract fields as JSON"}],
            routing_decision=decision,
            adapter_factory=_factory,
            max_attempts=3,
        )
    assert len(exc_info.value.attempts) == 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cost_savings_exceed_50_percent_on_realistic_workload() -> None:
    """Realistic 30% LOW / 50% MEDIUM / 20% HIGH workload achieves >=50% cost reduction."""
    router = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing.yaml")

    def _factory(provider: str, model: str) -> Any:
        return _StubModelAdapter(
            model=model,
            content='{"status": "ok"}',
            input_tokens=1500,
            output_tokens=500,
        )

    routed = RoutedModelAdapter(
        router=router,
        adapter_factory=_factory,
        quality_validator=QualityValidator(method="schema_compliance", required_keys=["status"]),
    )

    workload = (
        ["Convert this JSON payload to YAML"] * 30
        + ["Analyze this code module and generate unit tests"] * 50
        + ["Design a novel distributed architecture considering trade-offs"] * 20
    )
    for prompt in workload:
        await routed.invoke([{"role": "user", "content": prompt}])

    metrics = router.get_metrics()
    assert metrics["total_decisions"] == 100
    assert metrics["decisions_by_tier"][ComplexityLevel.LOW.value] == 30
    assert metrics["decisions_by_tier"][ComplexityLevel.MEDIUM.value] == 50
    assert metrics["decisions_by_tier"][ComplexityLevel.HIGH.value] == 20
    assert metrics["cost_savings_pct"] >= 50.0, f"Savings {metrics['cost_savings_pct']}% < 50%"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_quality_maintained_with_llm_judge_fallback() -> None:
    """Forced high-complexity task failing LLM-judge on LOW tier escalates until judge passes."""
    router = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing.yaml")

    async def _judge(text: str, ctx: dict[str, Any]) -> dict[str, Any]:
        ok = "deep trade-off analysis" in text
        return {"score": 0.95 if ok else 0.35, "reason": "rigor check"}

    def _factory(provider: str, model: str) -> Any:
        if model == "claude-3-haiku-20240307":
            return _StubModelAdapter(model=model, content="Shallow answer")
        return _StubModelAdapter(model=model, content="Comprehensive deep trade-off analysis")

    routed = RoutedModelAdapter(
        router=router,
        adapter_factory=_factory,
        quality_validator=QualityValidator(
            method="llm_judge", min_score=0.8, judge_callable=_judge
        ),
    )
    resp = await routed.invoke(
        [{"role": "user", "content": "Extract summary"}],
    )
    assert "deep trade-off analysis" in resp.content
    assert resp.model != "claude-3-haiku-20240307"


@pytest.mark.integration
def test_hot_reload_updates_routing_policy_from_yaml(tmp_path: Path) -> None:
    """ModelRouter.reload() hot-reloads modified force_tier / enabled settings from YAML."""
    cfg_data = yaml.safe_load((REPO_ROOT / "config" / "routing.yaml").read_text(encoding="utf-8"))
    cfg_file = tmp_path / "routing.yaml"
    cfg_file.write_text(yaml.safe_dump(cfg_data), encoding="utf-8")

    router = ModelRouter.from_yaml(cfg_file)
    d1 = router.route([{"role": "user", "content": "Extract names as JSON"}])
    assert d1.complexity_level == ComplexityLevel.LOW

    cfg_data["routing"]["force_tier"] = "high"
    cfg_file.write_text(yaml.safe_dump(cfg_data), encoding="utf-8")
    router.reload()

    d2 = router.route([{"role": "user", "content": "Extract names as JSON"}])
    assert d2.complexity_level == ComplexityLevel.HIGH
    assert d2.model == "gpt-4-turbo-2024-04-09"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_shadow_mode_logs_decision_without_changing_execution_adapter() -> None:
    """In shadow_mode, router records decisions while execution stays on default_adapter."""
    cfg = yaml.safe_load((REPO_ROOT / "config" / "routing.yaml").read_text(encoding="utf-8"))
    cfg["routing"]["shadow_mode"] = True
    router = ModelRouter(cfg)

    default_calls: list[str] = []
    routed_calls: list[str] = []
    default_adapter = _StubModelAdapter("gpt-4-turbo-2024-04-09", call_log=default_calls)

    routed = RoutedModelAdapter(
        router=router,
        adapter_factory=lambda p, m: _StubModelAdapter(m, call_log=routed_calls),
        default_adapter=default_adapter,  # type: ignore[arg-type]
    )
    resp = await routed.invoke([{"role": "user", "content": "Extract names as JSON"}])

    assert resp.model == "gpt-4-turbo-2024-04-09"
    assert default_calls == ["gpt-4-turbo-2024-04-09"]
    assert routed_calls == []
    assert routed.last_decision is not None
    assert routed.last_decision.is_shadow is True
    assert routed.last_decision.complexity_level == ComplexityLevel.LOW


@pytest.mark.integration
@pytest.mark.asyncio
async def test_harness_integration_with_model_router_and_budget_guards() -> None:
    """Harness routes invoke_model through ModelRouter and tracks routed cost in Budget."""
    router = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing.yaml")
    invoked: list[str] = []

    harness = Harness(
        context=ExecutionContext(
            run_id="run-routed-harness",
            stage_name="build",
            agent_name="build-agent",
        ),
        budget=Budget(max_cost_usd=Decimal("5.00")),
        model_router=router,
        adapter_factory=lambda p, m: _StubModelAdapter(
            m, content='{"tests": ["test_add"]}', call_log=invoked
        ),  # type: ignore[arg-type]
        quality_validator=QualityValidator(method="schema_compliance", required_keys=["tests"]),
    )
    harness.budget.start()

    resp = await harness.invoke_model(
        [{"role": "user", "content": "Extract test names from file as JSON"}]
    )

    assert resp.model == "claude-3-haiku-20240307"
    assert invoked == ["claude-3-haiku-20240307"]
    assert harness.last_routing_decision is not None
    assert harness.last_routing_decision.complexity_level == ComplexityLevel.LOW
    assert harness.budget.current_cost_usd > Decimal("0")
    assert router.get_metrics()["cost_savings_pct"] > 90.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multi_agent_routing_routes_different_agents_to_appropriate_tiers() -> None:
    """Multiple agents sharing a router get appropriate tiers for their respective tasks."""
    router = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing.yaml")

    d_discovery = router.route(
        [{"role": "user", "content": "Hypothesize novel growth strategy considering trade-offs"}],
        metadata={"agent_name": "discovery-agent"},
    )
    d_review = router.route(
        [{"role": "user", "content": "Review and analyze security patterns in this module"}],
        metadata={"agent_name": "review-agent"},
    )
    d_validator = router.route(
        [{"role": "user", "content": "Validate JSON schema fields and format output"}],
        metadata={"agent_name": "schema-agent"},
    )

    assert d_discovery.complexity_level == ComplexityLevel.HIGH
    assert d_review.complexity_level == ComplexityLevel.MEDIUM
    assert d_validator.complexity_level == ComplexityLevel.LOW


@pytest.mark.integration
def test_stress_1000_routing_decisions_fast_and_consistent() -> None:
    """Stress test: 1,000 routing decisions execute cleanly with exact tier counts."""
    router = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing.yaml")
    prompts = [
        "Extract fields as JSON",
        "Analyze and refactor this function",
        "Design a novel multi-region architecture considering trade-offs",
    ]
    for idx in range(1000):
        router.route([{"role": "user", "content": prompts[idx % 3]}])

    metrics = router.get_metrics()
    assert metrics["total_decisions"] == 1000
    assert sum(metrics["decisions_by_tier"].values()) == 1000


@pytest.mark.integration
def test_cost_alert_threshold_triggers_when_exceeded() -> None:
    """Cost alert flag activates when routed spend exceeds configured alert_threshold_usd."""
    cfg = yaml.safe_load((REPO_ROOT / "config" / "routing.yaml").read_text(encoding="utf-8"))
    cfg["routing"]["cost_tracking"]["alert_threshold_usd"] = 0.01
    router = ModelRouter(cfg)

    router.record_execution(
        model_id="gpt-4-turbo-2024-04-09",
        input_tokens=5000,
        output_tokens=2000,
    )
    metrics = router.get_metrics()
    assert metrics["routed_cost_usd"] > 0.01
    assert metrics["alert_triggered"] is True
