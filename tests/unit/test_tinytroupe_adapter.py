"""
Unit tests for TinyTroupe simulation module (EVO-009):
TinyTroupeAdapter, PersonaFactory, PopulationGenerator, SimulationValidator, ScenarioRunner.
"""

from pathlib import Path

import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.harness.budget import BudgetExceededError
from product_value_system.simulation.persona_factory import PersonaFactory
from product_value_system.simulation.population_generator import (
    DemographicSegment,
    PopulationGenerator,
    PopulationSpec,
)
from product_value_system.simulation.scenario_runner import ScenarioRunner
from product_value_system.simulation.simulation_validator import SimulationValidator
from product_value_system.simulation.tinytroupe_adapter import (
    TINYTROUPE_AVAILABLE,
    PersonaDefinition,
    SimulationResult,
    TinyTroupeAdapter,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_PATH = REPO_ROOT / "config" / "simulation" / "persona_templates.yaml"
CRITERIA_PATH = REPO_ROOT / "config" / "simulation" / "validation_criteria.yaml"


@pytest.mark.unit
def test_persona_definition_creation() -> None:
    """Test PersonaDefinition dataclass stores demographics, personality, and behaviors."""
    persona = PersonaDefinition(
        name="Alice",
        demographics={"age": 35, "education": "Bachelor's"},
        personality={"openness": "high", "conscientiousness": "medium"},
        behaviors={"technology_adoption": "early"},
        context_awareness={"device_types": ["iPhone"]},
        accessibility_needs=["Large font support"],
    )

    assert persona.name == "Alice"
    assert persona.demographics["age"] == 35
    assert persona.personality["openness"] == "high"
    assert persona.accessibility_needs == ["Large font support"]


@pytest.mark.unit
def test_tinytroupe_adapter_initialization() -> None:
    """Test adapter initialization defaults and budget state."""
    adapter = TinyTroupeAdapter(max_cost_usd=50.0)
    assert adapter.max_cost_usd == 50.0
    assert adapter.spent_usd == 0.0
    assert adapter.enable_caching is True


@pytest.mark.unit
def test_require_tinytroupe_raises_when_absent() -> None:
    """Test require_tinytroupe=True raises helpful ImportError if package not installed."""
    if TINYTROUPE_AVAILABLE:
        adapter = TinyTroupeAdapter(require_tinytroupe=True)
        assert adapter is not None
    else:
        with pytest.raises(ImportError, match="pip install tinytroupe"):
            TinyTroupeAdapter(require_tinytroupe=True)


@pytest.mark.unit
def test_create_persona_and_batch() -> None:
    """Test creating single and multiple persona instances from definitions."""
    adapter = TinyTroupeAdapter()
    defs = [
        PersonaDefinition(
            name="Bob",
            demographics={"age": 40, "income": 80000},
            personality={"openness": "medium"},
            behaviors={"technology_adoption": "mainstream"},
            context_awareness={"pain_points": ["Slow performance"]},
        ),
        PersonaDefinition(
            name="Clara",
            demographics={"age": 62},
            personality={"openness": "low"},
            behaviors={"technology_adoption": "late_majority"},
            context_awareness={"pain_points": ["Small text"]},
            accessibility_needs=["Large font support"],
        ),
    ]

    personas = adapter.create_personas(defs)
    assert len(personas) == 2
    assert personas[0].name == "Bob"
    assert personas[0].get("age") == 40
    assert personas[1].get("accessibility_needs") == ["Large font support"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_scenario_local_fallback() -> None:
    """Test scenario execution produces interactions, insights, quality metrics, and disclaimer."""
    adapter = TinyTroupeAdapter(max_cost_usd=10.0)
    defs = [
        PersonaDefinition(
            name=f"User{i}",
            demographics={"age": 30 + i},
            personality={"openness": "high" if i % 2 == 0 else "low"},
            behaviors={"technology_adoption": "early" if i % 2 == 0 else "late_majority"},
            context_awareness={"pain_points": ["Slow performance"]},
        )
        for i in range(5)
    ]
    personas = adapter.create_personas(defs)
    scenario_config = {
        "interactions": [
            {"prompt": "How do you feel about instant payment notifications?"},
            {"prompt": "What concerns you most about mobile alerts?"},
        ]
    }

    result = await adapter.run_scenario("payment_check", personas, scenario_config)

    assert result.scenario_name == "payment_check"
    assert result.population_size == 5
    assert len(result.interactions) == 2
    assert result.aggregated_insights["response_count"] == 10
    assert result.cost_usd > 0
    assert result.cost_usd < 10.0
    assert "not a replacement for real user research" in result.disclaimer.lower()
    assert result.to_dict()["population_size"] == 5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_scenario_with_pvs_model_adapter() -> None:
    """Test TinyTroupeAdapter routes persona responses through a PVS ModelAdapter."""
    mock_llm = MockModelAdapter(
        responses=[
            "I love instant alerts! Ease of use and privacy are top priorities.",
            "I am worried about privacy concerns and want dark mode support.",
        ]
    )
    adapter = TinyTroupeAdapter(llm_adapter=mock_llm, max_cost_usd=5.0)
    personas = adapter.create_personas(
        [
            PersonaDefinition(name="P1", demographics={"age": 29}),
            PersonaDefinition(name="P2", demographics={"age": 48}),
        ]
    )

    result = await adapter.run_scenario(
        "llm_backed_focus_group",
        personas,
        {"interactions": [{"prompt": "Rate instant alerts."}]},
    )

    assert result.aggregated_insights["response_count"] == 2
    assert any(t["theme"] == "Privacy & security" for t in result.aggregated_insights["themes"])
    assert "Dark mode support" in result.aggregated_insights["feature_requests"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_budget_exceeded_error_halts_simulation() -> None:
    """Test BudgetExceededError is raised when simulation exceeds max_cost_usd."""
    adapter = TinyTroupeAdapter(max_cost_usd=0.002)
    personas = adapter.create_personas(
        [PersonaDefinition(name=f"P{i}") for i in range(5)]
    )

    with pytest.raises(BudgetExceededError, match="Simulation budget exceeded"):
        await adapter.run_scenario(
            "too_expensive",
            personas,
            {"interactions": [{"prompt": "Question 1"}, {"prompt": "Question 2"}]},
        )


@pytest.mark.unit
def test_aggregate_insights_themes_sentiment_pain_points() -> None:
    """Test insight extraction across themes, sentiment ratios, pain points, and features."""
    adapter = TinyTroupeAdapter()
    interactions = [
        {
            "prompt": "Q1",
            "responses": [
                "I feel enthusiastic and love the fast intuitive design! "
                "My primary concern is slow loading, and I want dark mode.",
                "I am worried about complex navigation and privacy concerns. "
                "My primary concern is small text, please add large font support.",
            ],
        }
    ]
    insights = adapter._aggregate_insights(interactions)

    assert insights["response_count"] == 2
    sentiment = insights["sentiment"]
    assert abs((sentiment["positive"] + sentiment["neutral"] + sentiment["negative"]) - 1.0) < 0.02
    assert len(insights["themes"]) >= 2
    assert len(insights["pain_points"]) >= 1
    assert "Dark mode support" in insights["feature_requests"]
    assert "Large font support" in insights["feature_requests"]


@pytest.mark.unit
def test_quality_metrics_bounds_and_empty_handling() -> None:
    """Test quality metrics stay within [0, 1] and handle empty interactions cleanly."""
    adapter = TinyTroupeAdapter()
    empty = adapter._compute_quality_metrics([], [])
    assert empty == {
        "persona_adherence": 0.0,
        "interaction_realism": 0.0,
        "response_diversity": 0.0,
    }


@pytest.mark.unit
def test_persona_factory_single_template_reproducible() -> None:
    """Test PersonaFactory generates seeded, in-range populations from YAML templates."""
    factory_a = PersonaFactory(TEMPLATES_PATH, seed=42)
    factory_b = PersonaFactory(TEMPLATES_PATH, seed=42)

    assert "tech_early_adopter" in factory_a.available_templates
    pop_a = factory_a.generate_population("tech_early_adopter", size=10)
    pop_b = factory_b.generate_population("tech_early_adopter", size=10)

    assert len(pop_a) == 10
    assert [p.demographics["age"] for p in pop_a] == [p.demographics["age"] for p in pop_b]
    for p in pop_a:
        assert 25 <= p.demographics["age"] <= 40
        assert 70000 <= p.demographics["income"] <= 150000


@pytest.mark.unit
def test_persona_factory_mixed_population_and_errors() -> None:
    """Test mixed population proportions exact size and error on unknown template."""
    factory = PersonaFactory(TEMPLATES_PATH, seed=7)
    mixed = factory.generate_mixed_population(
        {"tech_early_adopter": 0.3, "mainstream_user": 0.5, "accessibility_focused": 0.2},
        total_size=20,
    )
    assert len(mixed) == 20
    assert any(p.accessibility_needs for p in mixed)

    with pytest.raises(ValueError, match="not found"):
        factory.generate_population("nonexistent_segment", size=5)


@pytest.mark.unit
def test_population_generator_stratified_and_summary() -> None:
    """Test PopulationGenerator builds stratified segments with overrides and summary."""
    factory = PersonaFactory(TEMPLATES_PATH, seed=123)
    gen = PopulationGenerator(factory)
    spec = PopulationSpec(
        total_size=20,
        segments=[
            DemographicSegment(
                template="tech_early_adopter",
                proportion=0.5,
                overrides={"region": "US"},
            ),
            DemographicSegment(template="accessibility_focused", proportion=0.5),
        ],
    )
    pop = gen.generate(spec)
    assert len(pop) == 20
    assert sum(1 for p in pop if p.demographics.get("region") == "US") == 10

    summary = gen.summarize_population(pop)
    assert summary["size"] == 20
    assert summary["accessibility_representation"] == pytest.approx(0.5)
    assert summary["age_min"] is not None and summary["age_max"] is not None


@pytest.mark.unit
def test_simulation_validator_statistical_agreement_and_divergence() -> None:
    """Test SimulationValidator runs real Welch t-test and KS-test via scipy.stats."""
    validator = SimulationValidator(CRITERIA_PATH)

    # Identical / consistent distributions -> both tests should pass (p >= 0.05)
    sim_ok = [4.1, 4.3, 3.9, 4.0, 4.2, 4.4, 3.8, 4.1, 4.0, 4.2]
    real_ok = [4.0, 4.2, 3.9, 4.1, 4.3, 4.2, 3.9, 4.0, 4.1, 4.2]
    ok_tests = validator.compare_distributions(sim_ok, real_ok)
    assert len(ok_tests) == 2
    assert all(t.passed for t in ok_tests)

    # Clearly shifted distributions -> both tests should fail (p < 0.05)
    sim_bad = [4.8, 4.9, 5.0, 4.7, 4.9, 4.8, 5.0, 4.9, 4.8, 4.9]
    real_bad = [1.2, 1.5, 1.3, 1.1, 1.4, 1.2, 1.6, 1.3, 1.4, 1.2]
    bad_tests = validator.compare_distributions(sim_bad, real_bad)
    assert not any(t.passed for t in bad_tests)


@pytest.mark.unit
def test_simulation_validator_full_report_and_warnings() -> None:
    """Test ValidationReport evaluates sample size warnings, quality checks, and confidence."""
    validator = SimulationValidator(CRITERIA_PATH)
    sim_result = SimulationResult(
        scenario_name="sample_eval",
        population_size=15,  # Below 50 -> warning expected
        interactions=[{"prompt": "Q1", "responses": ["A"]}],  # Below 3 -> warning expected
        aggregated_insights={"response_count": 15},
        quality_metrics={
            "persona_adherence": 0.90,
            "interaction_realism": 0.85,
            "response_diversity": 0.80,
        },
        cost_usd=0.05,
        duration_seconds=0.2,
    )

    report = validator.validate(
        sim_result,
        simulated_scores=[4.0, 4.1, 3.9, 4.2, 4.0],
        real_scores=[4.1, 4.0, 3.9, 4.1, 4.0],
    )
    assert report.passed is True
    assert report.confidence_level == "high"
    assert report.confidence_score >= 0.80
    assert len(report.warnings) == 2
    assert "statistical_tests" in report.to_dict()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scenario_runner_hypothesis_validation_domain_inference() -> None:
    """Test ScenarioRunner infers banking population mix and executes 3 validation prompts."""
    adapter = TinyTroupeAdapter(max_cost_usd=10.0)
    factory = PersonaFactory(TEMPLATES_PATH, seed=99)
    runner = ScenarioRunner(adapter=adapter, factory=factory)

    result = await runner.run_hypothesis_validation(
        outcome_hypothesis={
            "hypothesis": "Instant push notifications cut payment support calls by 40%."
        },
        problem_context={"user_segment": "Mobile banking users", "domain": "payments"},
        population_size=10,
    )
    assert result.scenario_name == "hypothesis_validation"
    assert result.population_size == 10
    assert len(result.interactions) == 3
    assert result.aggregated_insights["response_count"] == 30
