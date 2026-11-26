"""
Tests for Versioned YAML PromptRegistry, LLM-as-a-Judge Human Calibration
(Cohen's Kappa & Positional Bias), and DSPy-style PromptOptimizer (EVO-P3-13).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.evaluation.ai_judges import CompletenessJudge
from product_value_system.evaluation.judge_calibration import (
    CalibrationSample,
    PairwiseComparisonSample,
    calibrate_judge,
    compute_cohens_kappa,
    measure_positional_bias,
)
from product_value_system.evaluation.prompt_optimizer import (
    LabeledExample,
    PromptOptimizer,
)
from product_value_system.evaluation.prompt_registry import (
    PromptRegistry,
    PromptTemplate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_prompt_registry_loads_yaml_versions_and_renders_safely() -> None:
    registry = PromptRegistry.from_directory(REPO_ROOT / "prompts")
    assert "discovery.opportunity_synthesis" in registry.list_prompts()
    assert registry.list_versions("discovery.opportunity_synthesis") == ["1.0.0", "1.1.0"]

    latest = registry.get("discovery.opportunity_synthesis")
    assert latest.version == "1.1.0"
    assert latest.cache_version_key.startswith("discovery.opportunity_synthesis@1.1.0:")

    pinned = registry.get("discovery.opportunity_synthesis", version="1.0.0")
    assert pinned.version == "1.0.0"
    assert pinned.content_sha256 != latest.content_sha256

    sys_txt, user_txt = pinned.render(
        problem_statement="High checkout drop-off",
        evidence_summary="42% abandon at payment step",
    )
    assert "Product Discovery Specialist" in sys_txt
    assert "High checkout drop-off" in user_txt

    with pytest.raises(KeyError, match="missing required variable"):
        latest.render(problem_statement="Only one var")


@pytest.mark.asyncio
async def test_judge_human_calibration_cohens_kappa_and_positional_bias() -> None:
    """Calibrates CompletenessJudge with Cohen's Kappa and detects pairwise positional bias."""
    assert compute_cohens_kappa([True, True, False, False], [True, True, False, False]) == 1.0
    assert compute_cohens_kappa([True, False, True, False], [True, True, False, False]) == 0.0

    mock_adapter = MockModelAdapter(
        responses=[
            json.dumps({"score": 0.90, "rationale": "Complete spec"}),
            json.dumps({"score": 0.85, "rationale": "Solid spec"}),
            json.dumps({"score": 0.30, "rationale": "Missing acceptance criteria"}),
            json.dumps({"score": 0.25, "rationale": "Incomplete"}),
            json.dumps({"score": 0.92, "rationale": "Comprehensive"}),
        ]
    )
    judge = CompletenessJudge(mock_adapter)
    samples = [
        CalibrationSample("s1", {"id": "SPEC-1"}, human_passed=True, human_score=0.92),
        CalibrationSample("s2", {"id": "SPEC-2"}, human_passed=True, human_score=0.80),
        CalibrationSample("s3", {"id": "SPEC-3"}, human_passed=False, human_score=0.35),
        CalibrationSample("s4", {"id": "SPEC-4"}, human_passed=False, human_score=0.20),
        CalibrationSample("s5", {"id": "SPEC-5"}, human_passed=True, human_score=0.90),
    ]

    report = await calibrate_judge(judge, samples, kappa_threshold=0.80)
    assert report.passed_calibration is True
    assert report.cohens_kappa == 1.0
    assert report.exact_agreement == 1.0
    assert report.mae < 0.05

    pairs = [
        PairwiseComparisonSample("p1", "Detailed spec with SLOs", "Vague 1-liner"),
        PairwiseComparisonSample("p2", "Short stub", "Full ADR with trade-offs"),
        PairwiseComparisonSample("p3", "Equal quality A", "Equal quality B"),
    ]

    # Unbiased judge prefers longer detailed output regardless of A/B order
    def _unbiased(a: str, b: str) -> str:
        if len(a) > len(b):
            return "A"
        if len(b) > len(a):
            return "B"
        return "TIE"

    unbiased_report = await measure_positional_bias(_unbiased, pairs, max_bias_rate=0.10)
    assert unbiased_report.passed is True
    assert unbiased_report.positional_bias_rate == 0.0

    # Position-biased judge always picks "A" -> fails swap check on every pair
    biased_report = await measure_positional_bias(lambda _a, _b: "A", pairs, max_bias_rate=0.10)
    assert biased_report.passed is False
    assert biased_report.positional_bias_rate == 1.0


@pytest.mark.asyncio
async def test_dspy_style_prompt_optimizer_achieves_empirical_uplift() -> None:
    """PromptOptimizer bootstraps verified demos and selects highest-scoring candidate."""
    base = PromptTemplate(
        id="spec.classifier",
        version="1.0.0",
        agent="specification-agent",
        system_prompt="Classify the requirement priority.",
        user_template="Requirement: {text}",
    )
    train = [
        LabeledExample("tr-1", {"text": "Checkout outage recovery"}, "P0"),
        LabeledExample("tr-2", {"text": "Update footer copyright year"}, "P3"),
    ]
    val = [
        LabeledExample("v-1", {"text": "Database failover automation"}, "P0"),
        LabeledExample("v-2", {"text": "Rename tooltip label"}, "P3"),
    ]

    # Simulated model improves significantly when given structured rubric + few-shot demos
    def _runner(template: PromptTemplate, inputs: dict[str, Any]) -> str:
        text = str(inputs["text"]).lower()
        has_demos = "Verified Few-Shot Demonstrations" in template.system_prompt
        has_rubric = "P0 for reliability" in template.system_prompt
        if "outage" in text:
            return "P0"
        if "copyright" in text:
            return "P3"
        if has_demos and has_rubric:
            return "P0" if "failover" in text else "P3"
        if has_rubric:
            return "P0" if "failover" in text else "P1"
        return "P2"

    def _exact_match(pred: str, expected: str, _inputs: dict[str, Any]) -> float:
        return 1.0 if pred.strip() == expected.strip() else 0.0

    optimizer = PromptOptimizer(_exact_match, min_demo_score=0.9, max_bootstrapped_demos=2)
    result = await optimizer.optimize(
        base,
        train,
        val,
        runner=_runner,
        candidate_instructions=[
            "Classify priority: use P0 for reliability/failover and P3 for cosmetic copy."
        ],
    )

    assert result.baseline_score == 0.0
    assert result.optimized_score == 1.0
    assert result.improvement_delta == 1.0
    assert len(result.selected_demo_ids) == 2
    assert "Verified Few-Shot Demonstrations" in result.best_template.system_prompt
    assert len(result.trials) >= 3
