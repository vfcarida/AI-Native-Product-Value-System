"""
Unit tests for EVO-016: Experiment Design Agent, Power Analysis, A/B Testing & Guardrails.
"""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import numpy as np
import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.agents.experiment_design_agent import ExperimentDesignAgent
from product_value_system.experiment.ab_test import ABTest
from product_value_system.experiment.models import (
    ExperimentDesign,
    ExperimentHypothesis,
    ExperimentStatus,
    GuardrailMetric,
    StatisticalTestType,
    Variant,
)
from product_value_system.experiment.power_analysis import PowerAnalyzer
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_design(
    *,
    exp_id: str = "experiment-unit-001",
    mde: float = 0.10,
    duration_days: int = 14,
    guardrails: list[GuardrailMetric] | None = None,
    test_type: StatisticalTestType = StatisticalTestType.TTEST,
) -> ExperimentDesign:
    hypothesis = ExperimentHypothesis(
        primary_metric="checkout_conversion",
        baseline_value=0.45,
        expected_value=0.75,
        minimum_detectable_effect=mde,
        confidence_level=0.95,
        statistical_power=0.80,
    )
    return ExperimentDesign(
        id=exp_id,
        hypothesis=hypothesis,
        sample_size_per_variant=500,
        duration_days=duration_days,
        guardrails=guardrails or [],
        test_type=test_type,
    )


@pytest.mark.unit
def test_power_analysis_ttest_sample_size_calculation() -> None:
    """Two-sample t-test power formula returns realistic sample sizes and scales with MDE/power."""
    hypothesis = ExperimentHypothesis(
        baseline_value=0.45,
        expected_value=0.75,
        minimum_detectable_effect=0.05,
        confidence_level=0.95,
        statistical_power=0.80,
    )
    agent = ExperimentDesignAgent()
    sample_size = agent._compute_sample_size(hypothesis)
    assert 50 <= sample_size <= 50000

    # Smaller MDE or higher statistical power strictly requires larger sample size
    tighter_mde = PowerAnalyzer.compute_ttest_sample_size(0.45, 0.02)
    higher_power = PowerAnalyzer.compute_ttest_sample_size(0.45, 0.05, statistical_power=0.95)
    assert tighter_mde > sample_size
    assert higher_power > sample_size


@pytest.mark.unit
def test_power_analysis_proportion_sample_size_calculation() -> None:
    """Two-proportion chi-square power calculation sizes binary conversion rates accurately."""
    n_10pct_lift = PowerAnalyzer.compute_proportion_sample_size(
        baseline_rate=0.20,
        minimum_detectable_effect=0.10,  # 20% -> 22%
        confidence_level=0.95,
        statistical_power=0.80,
    )
    # Standard textbook value for p1=0.20, p2=0.22, alpha=0.05, power=0.80 is ~6,500
    assert 5500 <= n_10pct_lift <= 7500


@pytest.mark.unit
def test_power_analysis_duration_estimation_clamping() -> None:
    """Duration estimator clamps to [7, 30] days."""
    assert PowerAnalyzer.estimate_duration_days(100, daily_traffic=5000) == 7
    assert PowerAnalyzer.estimate_duration_days(100000, daily_traffic=500) == 30
    assert PowerAnalyzer.estimate_duration_days(7000, daily_traffic=1000) == 14


@pytest.mark.unit
def test_experiment_design_agent_builds_hypothesis_from_both_outcome_shapes() -> None:
    """Agent extracts hypothesis from both official outcome.schema.json and simplified dict."""
    agent = ExperimentDesignAgent()

    schema_outcome = {
        "id": "outcome-pay-01",
        "hypothesis": "Reducing checkout steps increases completed payment conversions.",
        "success_criteria": {
            "primary_metric": {
                "name": "payment_conversion_rate",
                "unit": "ratio",
                "measurement_method": "analytics",
            },
            "baseline": 0.40,
            "target": 0.50,
        },
    }
    hyp_schema = agent._build_hypothesis(schema_outcome)
    assert hyp_schema.primary_metric == "payment_conversion_rate"
    assert hyp_schema.minimum_detectable_effect == pytest.approx(0.25)
    assert hyp_schema.metric_type == "proportion"

    simple_outcome = {
        "hypothesis": "Faster search increases orders per user.",
        "primary_metric": {"name": "orders_per_user", "baseline": 2.0, "target": 2.4},
    }
    hyp_simple = agent._build_hypothesis(simple_outcome)
    assert hyp_simple.primary_metric == "orders_per_user"
    assert hyp_simple.minimum_detectable_effect == pytest.approx(0.20)
    assert hyp_simple.metric_type == "continuous"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_experiment_design_agent_parses_llm_guardrails_and_validates_schema() -> None:
    """Agent parses LLM-suggested JSON guardrails and outputs schema-valid ExperimentDesign."""
    llm_guardrails = json.dumps(
        [
            {
                "name": "revenue_per_user",
                "threshold": -0.05,
                "direction": "decrease",
                "description": "Ensure checkout changes do not drop revenue >5%",
            },
            {
                "name": "checkout_error_rate",
                "threshold": 0.01,
                "direction": "increase",
                "description": "Ensure error rate does not rise >1%",
            },
        ]
    )
    adapter = MockModelAdapter(responses=[llm_guardrails])
    ctx = ExecutionContext(run_id="exp-unit", agent_name="experiment-design-agent")
    harness = Harness(context=ctx, model_adapter=adapter)
    agent = ExperimentDesignAgent()

    outcome = {
        "id": "outcome-checkout-01",
        "hypothesis": "One-click checkout increases conversion rate from 45% to 55%.",
        "primary_metric": {"name": "checkout_conversion", "baseline": 0.45, "target": 0.55},
        "daily_traffic": 2000,
    }
    result = await agent.execute(outcome, harness, ctx)

    assert result.success is True
    design_dict = result.output_artifact
    schema = json.loads(
        (REPO_ROOT / "schemas" / "experiment_design.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(instance=design_dict, schema=schema)
    assert [g["name"] for g in design_dict["guardrails"]] == [
        "revenue_per_user",
        "checkout_error_rate",
    ]


@pytest.mark.unit
def test_ab_test_variant_assignment_consistent_balanced_and_fast() -> None:
    """Consistent SHA-256 assignment is deterministic, balanced ~50/50, and <1ms."""
    ab_test = ABTest(_make_design(exp_id="experiment-hash-01"))

    v1 = ab_test.assign_variant("user-123")
    for _ in range(50):
        assert ab_test.assign_variant("user-123") == v1

    start = time.perf_counter()
    assignments = [ab_test.assign_variant(f"user-{i}") for i in range(1000)]
    avg_ms = ((time.perf_counter() - start) * 1000.0) / 1000.0
    assert avg_ms < 1.0

    treatment_count = sum(1 for v in assignments if v == Variant.TREATMENT)
    assert 440 <= treatment_count <= 560


@pytest.mark.unit
def test_ab_test_ttest_statistical_and_practical_significance_adopt() -> None:
    """Significant lift exceeding MDE produces recommendation='adopt' and 95% CI."""
    ab_test = ABTest(_make_design(mde=0.10))
    rng = np.random.default_rng(42)
    ab_test.data["control"] = list(rng.normal(0.45, 0.10, 1000))
    ab_test.data["treatment"] = list(rng.normal(0.75, 0.10, 1000))

    result = ab_test.analyze(test_type=StatisticalTestType.TTEST)

    assert result.statistically_significant is True
    assert result.practically_significant is True
    assert result.p_value < 0.05
    assert result.lift > 0.50
    assert result.confidence_interval[0] < result.lift < result.confidence_interval[1]
    assert result.recommendation == "adopt"


@pytest.mark.unit
def test_ab_test_statistically_significant_but_below_mde_rejects() -> None:
    """Statistically significant lift below practical MDE yields recommendation='reject'."""
    ab_test = ABTest(_make_design(mde=0.20))  # Requires >=20% relative lift
    rng = np.random.default_rng(7)
    ab_test.data["control"] = list(rng.normal(100.0, 2.0, 2000))
    ab_test.data["treatment"] = list(rng.normal(103.0, 2.0, 2000))  # Only +3% lift

    result = ab_test.analyze()

    assert result.statistically_significant is True
    assert result.practically_significant is False
    assert result.recommendation == "reject"
    assert "below mde" in result.reasoning.lower()


@pytest.mark.unit
def test_ab_test_inconclusive_result_recommends_continue() -> None:
    """No statistically significant difference recommends 'continue'."""
    ab_test = ABTest(_make_design(mde=0.10))
    rng = np.random.default_rng(99)
    ab_test.data["control"] = list(rng.normal(10.0, 3.0, 100))
    ab_test.data["treatment"] = list(rng.normal(10.1, 3.0, 100))

    result = ab_test.analyze()

    assert result.statistically_significant is False
    assert result.p_value >= 0.05
    assert result.recommendation == "continue"


@pytest.mark.unit
def test_ab_test_chi_square_conversion_analysis() -> None:
    """Chi-square test accurately detects significant binary conversion lifts."""
    ab_test = ABTest(_make_design(mde=0.10, test_type=StatisticalTestType.CHI_SQUARE))
    # Control: 200/1000 (20%), Treatment: 300/1000 (30%) -> +50% relative lift
    ab_test.data["control"] = [1.0] * 200 + [0.0] * 800
    ab_test.data["treatment"] = [1.0] * 300 + [0.0] * 700

    result = ab_test.analyze(test_type=StatisticalTestType.CHI_SQUARE)

    assert result.test_type == "chi_square"
    assert result.statistically_significant is True
    assert result.p_value < 0.001
    assert result.lift == pytest.approx(0.50)
    assert result.recommendation == "adopt"


@pytest.mark.unit
def test_ab_test_bayesian_binary_and_continuous_analysis() -> None:
    """Bayesian inference returns P(treatment > control) and 95% credible intervals."""
    # Binary Beta-Binomial posterior
    ab_bin = ABTest(_make_design(mde=0.10, test_type=StatisticalTestType.BAYESIAN))
    ab_bin.data["control"] = [1.0] * 180 + [0.0] * 820
    ab_bin.data["treatment"] = [1.0] * 260 + [0.0] * 740

    res_bin = ab_bin.analyze(test_type=StatisticalTestType.BAYESIAN, seed=42)
    assert res_bin.bayesian_prob_treatment_beats_control is not None
    assert res_bin.bayesian_prob_treatment_beats_control > 0.99
    assert res_bin.bayesian_credible_interval is not None
    assert res_bin.bayesian_credible_interval[0] > 0.0
    assert res_bin.recommendation == "adopt"

    # Continuous Normal-Normal posterior
    ab_cont = ABTest(_make_design(mde=0.05, test_type=StatisticalTestType.BAYESIAN))
    rng = np.random.default_rng(123)
    ab_cont.data["control"] = list(rng.normal(50.0, 5.0, 500))
    ab_cont.data["treatment"] = list(rng.normal(56.0, 5.0, 500))

    res_cont = ab_cont.analyze(test_type=StatisticalTestType.BAYESIAN, seed=42)
    assert res_cont.bayesian_prob_treatment_beats_control is not None
    assert res_cont.bayesian_prob_treatment_beats_control > 0.99
    assert res_cont.recommendation == "adopt"


@pytest.mark.unit
def test_guardrail_violation_decrease_and_increase_rejects_and_alerts() -> None:
    """Guardrail violations on decrease or increase reject experiment and fire alerts."""
    guardrails = [
        GuardrailMetric(
            name="revenue",
            threshold=-0.05,
            direction="decrease",
            description="Prevent >5% revenue drop",
        ),
        GuardrailMetric(
            name="error_rate",
            threshold=0.02,
            direction="increase",
            description="Prevent >2% error spike",
        ),
    ]
    alerts: list[str] = []
    ab_test = ABTest(_make_design(guardrails=guardrails), alert_callback=lambda m: alerts.append(m))
    ab_test.data["control"] = [1.0] * 50 + [0.5] * 50
    ab_test.data["treatment"] = [2.0] * 50 + [1.5] * 50

    ab_test.set_guardrail_summary("revenue", control_value=100.0, treatment_value=90.0)  # -10%
    ab_test.set_guardrail_summary("error_rate", control_value=0.01, treatment_value=0.015)  # +50%

    result = ab_test.analyze()
    assert len(result.guardrail_violations) == 2
    assert result.recommendation == "reject"
    assert len(alerts) == 2


@pytest.mark.unit
def test_stopping_criteria_early_stop_guardrail_and_max_duration() -> None:
    """Verify all 3 stopping triggers: guardrail abort, max duration, and p<0.01 early stop."""
    # 1. Early stop on strong evidence (p < 0.01 & lift >= MDE)
    ab_early = ABTest(_make_design(mde=0.10))
    ab_early.start()
    rng = np.random.default_rng(1)
    ab_early.data["control"] = list(rng.normal(10.0, 1.0, 400))
    ab_early.data["treatment"] = list(rng.normal(13.0, 1.0, 400))

    stop, reason = ab_early.should_stop_early()
    assert stop is True
    assert "p < 0.01" in reason
    assert ab_early.status == ExperimentStatus.STOPPED_EARLY

    # 2. Guardrail violation stops immediately and aborts
    guardrail = GuardrailMetric(name="revenue", threshold=-0.05, direction="decrease")
    ab_guard = ABTest(_make_design(guardrails=[guardrail]))
    ab_guard.start()
    ab_guard.set_guardrail_summary("revenue", control_value=100.0, treatment_value=85.0)
    stop_g, reason_g = ab_guard.should_stop_early()
    assert stop_g is True
    assert "revenue" in reason_g
    assert ab_guard.status == ExperimentStatus.ABORTED

    # 3. Max duration reached completes experiment
    ab_dur = ABTest(_make_design(duration_days=14))
    start_time = datetime.now(UTC) - timedelta(days=15)
    ab_dur.start(started_at=start_time)
    stop_d, reason_d = ab_dur.should_stop_early()
    assert stop_d is True
    assert "Max duration" in reason_d
    assert ab_dur.status == ExperimentStatus.COMPLETED


@pytest.mark.unit
def test_ab_test_analysis_latency_under_1s_for_10k_samples() -> None:
    """Verify statistical analysis for N=10,000 per variant runs well under 1 second."""
    ab_test = ABTest(_make_design(mde=0.05))
    rng = np.random.default_rng(42)
    ab_test.data["control"] = list(rng.normal(1.0, 0.2, 10000))
    ab_test.data["treatment"] = list(rng.normal(1.08, 0.2, 10000))

    start = time.perf_counter()
    result = ab_test.analyze()
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"Analysis for N=10,000 took {elapsed:.3f}s (>1s budget)"
    assert result.recommendation == "adopt"


@pytest.mark.unit
def test_ab_test_anonymizes_user_ids_in_exported_state() -> None:
    """Serialized state retains only aggregated numbers and never leaks raw user IDs."""
    ab_test = ABTest(_make_design())
    ab_test.record_metric("sensitive-user-email@example.com", 1.5)
    ab_test.record_metric("another-pii-id-999", 2.0)

    serialized = json.dumps(ab_test.to_state_dict())
    assert "sensitive-user-email@example.com" not in serialized
    assert "another-pii-id-999" not in serialized


@pytest.mark.unit
def test_experiment_result_format_report_includes_key_sections() -> None:
    """format_report() renders CI, p-value, MDE, guardrails, and recommendation."""
    ab_test = ABTest(_make_design(mde=0.10))
    rng = np.random.default_rng(11)
    ab_test.data["control"] = list(rng.normal(10.0, 1.0, 200))
    ab_test.data["treatment"] = list(rng.normal(12.0, 1.0, 200))

    result = ab_test.analyze()
    report = result.format_report()
    assert "ADOPT" in report
    assert "95% CI" in report
    assert "p-value" in report
    assert "Guardrails" in report
