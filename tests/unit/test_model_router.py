"""
Unit tests for ComplexityClassifier, RoutingPolicy, ModelRouter, and QualityValidator (EVO-026).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from product_value_system.adapters.model_adapter import Message, ModelResponse, Role, Usage
from product_value_system.routing import (
    ComplexityClassifier,
    ComplexityLevel,
    ModelRouter,
    QualityValidator,
    RoutingPolicy,
)
from product_value_system.security.secret_detection import SecretDetector

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def router() -> ModelRouter:
    return ModelRouter.from_yaml(REPO_ROOT / "config" / "routing.yaml")


@pytest.mark.unit
def test_low_complexity_extraction_classification() -> None:
    """Simple extraction prompt classifies as LOW complexity."""
    clf = ComplexityClassifier()
    score = clf.classify([{"role": "user", "content": "Extract function names from this code"}])
    assert score.level == ComplexityLevel.LOW
    assert score.score < 0.33
    assert "LOW" in score.reasoning


@pytest.mark.unit
def test_medium_complexity_code_analysis_classification() -> None:
    """Code analysis & pattern identification classifies as MEDIUM complexity."""
    clf = ComplexityClassifier()
    score = clf.classify(
        [
            {
                "role": "user",
                "content": "Analyze this codebase module and explain how to refactor and test it",
            }
        ]
    )
    assert score.level == ComplexityLevel.MEDIUM
    assert 0.33 <= score.score < 0.67


@pytest.mark.unit
def test_high_complexity_architecture_design_classification() -> None:
    """Strategic architecture design prompt classifies as HIGH complexity."""
    clf = ComplexityClassifier()
    score = clf.classify(
        [
            {
                "role": "user",
                "content": (
                    "Design a novel microservice architecture and strategize trade-offs "
                    "for brownfield modernization considering legacy database constraints"
                ),
            }
        ]
    )
    assert score.level == ComplexityLevel.HIGH
    assert score.score >= 0.67


@pytest.mark.unit
def test_schema_driven_task_reduces_complexity_score() -> None:
    """Structured schema/JSON formatting request scores lower than unstructured equivalent."""
    clf = ComplexityClassifier()
    unstructured = clf.classify([{"role": "user", "content": "Analyze customer feedback"}])
    structured = clf.classify(
        [{"role": "user", "content": "Analyze customer feedback and output JSON matching schema"}]
    )
    assert structured.features["schema_driven"] == 1.0
    assert unstructured.features["schema_driven"] == 0.0
    assert structured.score < unstructured.score


@pytest.mark.unit
def test_creative_and_reasoning_indicators_increase_complexity() -> None:
    """Creativity and multi-step reasoning keywords elevate complexity features & score."""
    clf = ComplexityClassifier()
    plain = clf.classify([{"role": "user", "content": "Review payment service"}])
    rich = clf.classify(
        [
            {
                "role": "user",
                "content": (
                    "Create a novel, innovative payment strategy considering architectural "
                    "trade-offs and root cause failure diagnosis"
                ),
            }
        ]
    )
    assert rich.features["creativity"] > plain.features["creativity"]
    assert rich.features["reasoning_depth"] > plain.features["reasoning_depth"]
    assert rich.score > plain.score


@pytest.mark.unit
def test_long_input_context_increases_complexity_score() -> None:
    """Very long prompt contexts raise input_length feature and overall complexity score."""
    clf = ComplexityClassifier()
    short_s = clf.classify([Message(role=Role.USER, content="Analyze this service")])
    long_body = "Detailed transaction event log record with latency telemetry. " * 900
    long_s = clf.classify([Message(role=Role.USER, content=f"Analyze this service:\n{long_body}")])

    assert long_s.token_count > 5000
    assert long_s.features["input_length"] > short_s.features["input_length"]
    assert long_s.score > short_s.score


@pytest.mark.unit
def test_code_block_complexity_feature_extraction() -> None:
    """Large fenced code blocks populate code_complexity feature."""
    clf = ComplexityClassifier()
    code_block = "```python\n" + ("def helper(x):\n    return x * 2\n" * 35) + "```"
    score = clf.classify([{"role": "user", "content": f"Refactor this module:\n{code_block}"}])
    assert score.features["code_complexity"] > 0.5


@pytest.mark.unit
def test_classification_accuracy_over_80_percent_on_labeled_dataset() -> None:
    """ComplexityClassifier achieves >=80% accuracy across 18 labeled LOW/MEDIUM/HIGH tasks."""
    clf = ComplexityClassifier()
    dataset: list[tuple[str, ComplexityLevel]] = [
        # 6 LOW
        ("Extract all function names and format as JSON", ComplexityLevel.LOW),
        ("Convert this YAML config to JSON schema format", ComplexityLevel.LOW),
        ("List all endpoints from the file", ComplexityLevel.LOW),
        ("Validate this payload against the JSON schema", ComplexityLevel.LOW),
        ("Count and filter duplicate error entries from CSV", ComplexityLevel.LOW),
        ("Fill the template fields with the provided values", ComplexityLevel.LOW),
        # 6 MEDIUM
        ("Analyze this module for code smells and refactor it", ComplexityLevel.MEDIUM),
        ("Generate unit tests for the payment service class", ComplexityLevel.MEDIUM),
        ("Explain how the retry backoff algorithm works", ComplexityLevel.MEDIUM),
        ("Document the public methods of the feature flag service", ComplexityLevel.MEDIUM),
        ("Optimize database queries in the order repository", ComplexityLevel.MEDIUM),
        ("Review this pull request for maintainability", ComplexityLevel.MEDIUM),
        # 6 HIGH
        ("Design a novel distributed event-driven architecture", ComplexityLevel.HIGH),
        ("Strategize a brownfield modernization plan considering trade-offs", ComplexityLevel.HIGH),
        ("Invent an innovative onboarding hypothesis for mobile banking", ComplexityLevel.HIGH),
        ("Architect a multi-region failover strategy for zero downtime", ComplexityLevel.HIGH),
        (
            "Create an original product roadmap plan analyzing root cause churn",
            ComplexityLevel.HIGH,
        ),
        (
            "Hypothesize creative solutions to reduce checkout latency considering trade-offs",
            ComplexityLevel.HIGH,
        ),
    ]

    correct = sum(
        1
        for prompt, expected in dataset
        if clf.classify([{"role": "user", "content": prompt}]).level == expected
    )
    accuracy = correct / len(dataset)
    assert accuracy >= 0.80, f"Classification accuracy {accuracy:.1%} below 80%"


@pytest.mark.unit
def test_routes_low_to_cheapest_low_tier_model(router: ModelRouter) -> None:
    """LOW complexity routes to cheapest low-tier model (claude-3-haiku)."""
    decision = router.route([{"role": "user", "content": "Convert this JSON payload to YAML"}])
    assert decision.complexity_level == ComplexityLevel.LOW
    assert decision.model == "claude-3-haiku-20240307"
    assert decision.provider == "anthropic"
    assert decision.estimated_cost_per_1k_tokens == pytest.approx(0.00025)


@pytest.mark.unit
def test_routes_medium_to_medium_tier_model(router: ModelRouter) -> None:
    """MEDIUM complexity routes to cheapest medium-tier model (claude-3-sonnet)."""
    decision = router.route(
        [{"role": "user", "content": "Analyze code patterns and write unit tests"}]
    )
    assert decision.complexity_level == ComplexityLevel.MEDIUM
    assert decision.model == "claude-3-sonnet-20240229"
    assert decision.provider == "anthropic"


@pytest.mark.unit
def test_routes_high_to_high_tier_model(router: ModelRouter) -> None:
    """HIGH complexity routes to cheapest high-tier model (gpt-4-turbo)."""
    decision = router.route(
        [
            {
                "role": "user",
                "content": (
                    "Design a novel microservice architecture and strategize trade-offs "
                    "for brownfield modernization"
                ),
            }
        ]
    )
    assert decision.complexity_level == ComplexityLevel.HIGH
    assert decision.model == "gpt-4-turbo-2024-04-09"
    assert decision.provider == "openai"


@pytest.mark.unit
def test_user_override_bypasses_classification_and_unknown_raises(router: ModelRouter) -> None:
    """user_override_model forces exact model; unknown model ID raises ValueError."""
    decision = router.route(
        [{"role": "user", "content": "Extract names"}],
        user_override_model="claude-3-opus-20240229",
    )
    assert decision.model == "claude-3-opus-20240229"
    assert decision.provider == "anthropic"
    assert decision.is_override is True
    assert decision.fallback_models == []

    with pytest.raises(ValueError, match="not found"):
        router.route([{"role": "user", "content": "Hi"}], user_override_model="nonexistent-model")


@pytest.mark.unit
def test_fallback_chain_escalates_across_tiers(router: ModelRouter) -> None:
    """LOW decision includes remaining LOW model plus MEDIUM and HIGH fallback models."""
    decision = router.route([{"role": "user", "content": "Format this list as JSON"}])
    assert decision.model == "claude-3-haiku-20240307"
    assert "gpt-3.5-turbo-0125" in decision.fallback_models
    assert "claude-3-sonnet-20240229" in decision.fallback_models
    assert "gpt-4-turbo-2024-04-09" in decision.fallback_models


@pytest.mark.unit
def test_preferred_provider_and_force_tier_rollback() -> None:
    """preferred_provider favors chosen vendor; force_tier='high' acts as safe rollback."""
    cfg_pref = {
        "routing": {
            "enabled": True,
            "preferred_provider": "openai",
            "models": RoutingPolicy.from_yaml(REPO_ROOT / "config" / "routing.yaml").raw_config[
                "models"
            ],
        }
    }
    pref_router = ModelRouter(cfg_pref)
    low_dec = pref_router.route([{"role": "user", "content": "Extract IDs as JSON"}])
    assert low_dec.provider == "openai"
    assert low_dec.model == "gpt-3.5-turbo-0125"

    cfg_force = {"routing": {**cfg_pref["routing"], "force_tier": "high"}}
    force_router = ModelRouter(cfg_force)
    forced = force_router.route([{"role": "user", "content": "Extract IDs as JSON"}])
    assert forced.complexity_level == ComplexityLevel.HIGH
    assert forced.is_override is True


@pytest.mark.unit
def test_cost_estimation_and_yaml_config_parsing(router: ModelRouter) -> None:
    """RoutingPolicy parses config/routing.yaml and estimates exact USD token costs."""
    assert router.enabled is True
    assert router.shadow_mode is False
    found = router.policy.find_model("claude-3-haiku-20240307")
    assert found is not None
    tier, spec = found
    assert tier == ComplexityLevel.LOW
    # 1M input ($0.25) + 1M output ($1.25) = $1.50
    assert spec.estimate_cost(1_000_000, 1_000_000) == pytest.approx(1.50)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_quality_validator_schema_compliance_pass_and_fail() -> None:
    """QualityValidator verifies JSON Schema compliance and required keys."""
    schema = {
        "type": "object",
        "properties": {"functions": {"type": "array"}},
        "required": ["functions"],
    }
    validator = QualityValidator(
        method="schema_compliance",
        json_schema=schema,
        required_keys=["functions"],
    )

    good = ModelResponse(
        content='```json\n{"functions": ["add", "sub"]}\n```', finish_reason="stop"
    )
    bad_json = ModelResponse(content="Not valid JSON at all", finish_reason="stop")
    missing_key = ModelResponse(content='{"classes": []}', finish_reason="stop")

    assert (await validator.validate(good)).passed is True
    assert (await validator.validate(bad_json)).passed is False
    assert (await validator.validate(missing_key)).passed is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_quality_validator_llm_judge_and_length_truncation() -> None:
    """QualityValidator supports async LLM judge scoring and flags truncated responses."""

    async def _judge(text: str, ctx: dict[str, Any]) -> dict[str, Any]:
        return {"score": 0.9 if "architecture" in text else 0.3, "reason": "depth check"}

    judge_val = QualityValidator(method="llm_judge", min_score=0.7, judge_callable=_judge)
    assert (await judge_val.validate(ModelResponse(content="Detailed architecture plan"))).passed
    assert not (await judge_val.validate(ModelResponse(content="Too vague"))).passed

    base_val = QualityValidator(min_length=5)
    truncated = ModelResponse(content="Cut off mid-sentence...", finish_reason="length")
    assert (await base_val.validate(truncated)).passed is False


@pytest.mark.unit
def test_routing_config_and_docs_contain_zero_secrets() -> None:
    """EVO-026 config and documentation contain zero detectable secrets."""
    detector = SecretDetector()
    for rel in ("config/routing.yaml", "docs/model-routing.md"):
        content = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert detector.find_secrets(content) == [], rel
        assert Usage(input_tokens=1, output_tokens=1).total_tokens == 2
