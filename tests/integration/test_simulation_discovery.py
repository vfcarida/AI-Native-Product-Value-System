"""
Integration tests for TinyTroupe customer simulation with DiscoveryAgent,
example scenarios, and CLI commands (EVO-009).
"""

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.agents.discovery_agent import DiscoveryAgent
from product_value_system.cli.main import main as cli_main
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.budget import Budget
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.simulation.persona_factory import PersonaFactory
from product_value_system.simulation.scenario_runner import ScenarioRunner
from product_value_system.simulation.tinytroupe_adapter import (
    TINYTROUPE_AVAILABLE,
    TinyTroupeAdapter,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "schemas"
TEMPLATES_PATH = REPO_ROOT / "config" / "simulation" / "persona_templates.yaml"


@pytest.fixture
def valid_outcome_json() -> str:
    """Valid outcome payload returned by mocked discovery LLM."""
    return json.dumps(
        {
            "hypothesis": (
                "Delivering instant mobile payment notifications within 2 seconds "
                "will reduce payment-status support calls by 35% within 90 days"
            ),
            "success_criteria": {
                "primary_metric": {
                    "name": "Payment status support call rate",
                    "unit": "percentage",
                    "measurement_method": "Support calls per 1000 mobile payments",
                },
                "baseline": 12.0,
                "target": 7.8,
                "guardrails": [
                    {
                        "metric": "Notification delivery latency",
                        "threshold": 2.0,
                        "direction": "below",
                    }
                ],
            },
            "evidence_requirements": [
                {
                    "type": "quantitative",
                    "description": "Validate adoption willingness across mobile banking segments",
                    "mandatory": True,
                }
            ],
        }
    )


@pytest.fixture
def payment_problem() -> dict[str, Any]:
    """Schema-valid payment notifications problem."""
    return {
        "id": "problem-payment-sim-001",
        "version": 1,
        "created": datetime.now(UTC).isoformat(),
        "statement": "Users lack timely payment notifications in the mobile banking app",
        "context": {
            "user_segment": "Mobile banking users",
            "business_context": "Reduce support volume and improve trust in payments",
        },
        "constraints": {
            "no_pii": True,
            "synthetic_only": True,
            "max_cost_usd": 20.0,
        },
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_discovery_agent_without_simulation_preserves_backwards_compat(
    payment_problem: dict[str, Any],
    valid_outcome_json: str,
) -> None:
    """DiscoveryAgent works identically with simulation disabled by default."""
    validator = SchemaValidator(SCHEMAS_DIR)
    agent = DiscoveryAgent(schema_validator=validator)
    harness = Harness(model_adapter=MockModelAdapter(responses=[valid_outcome_json]))
    context = ExecutionContext(run_id="sim-compat-001")

    outcome = await agent.execute(payment_problem, harness, context)
    assert "simulation_evidence" not in outcome
    validator.validate(outcome, "outcome.schema.json")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_discovery_agent_with_simulation_enabled(
    payment_problem: dict[str, Any],
    valid_outcome_json: str,
) -> None:
    """DiscoveryAgent with enable_simulation=True attaches validated simulation_evidence."""
    validator = SchemaValidator(SCHEMAS_DIR)
    sim_adapter = TinyTroupeAdapter(max_cost_usd=10.0)
    factory = PersonaFactory(TEMPLATES_PATH, seed=42)
    runner = ScenarioRunner(adapter=sim_adapter, factory=factory)

    agent = DiscoveryAgent(
        schema_validator=validator,
        enable_simulation=True,
        scenario_runner=runner,
    )
    budget = Budget(max_cost_usd=Decimal("10.0"))
    harness = Harness(
        model_adapter=MockModelAdapter(responses=[valid_outcome_json]),
        budget=budget,
    )
    context = ExecutionContext(run_id="sim-discovery-001")

    outcome = await agent.execute(payment_problem, harness, context)

    assert "simulation_evidence" in outcome
    sim = outcome["simulation_evidence"]
    assert sim["status"] == "completed"
    assert sim["population_size"] > 0
    assert "aggregated_insights" in sim
    assert "quality_metrics" in sim
    assert 0.0 <= sim["confidence"] <= 1.0
    assert sim["cost_usd"] > 0
    assert harness.budget.spent_usd >= sim["cost_usd"]
    validator.validate(outcome, "outcome.schema.json")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_discovery_agent_simulation_failure_degrades_gracefully(
    payment_problem: dict[str, Any],
    valid_outcome_json: str,
) -> None:
    """Simulation budget failure does not crash DiscoveryAgent outcome generation."""
    validator = SchemaValidator(SCHEMAS_DIR)
    # Intentionally tiny budget forces BudgetExceededError inside simulation
    tiny_adapter = TinyTroupeAdapter(max_cost_usd=0.0001)
    runner = ScenarioRunner(adapter=tiny_adapter, factory=PersonaFactory(TEMPLATES_PATH, seed=1))

    agent = DiscoveryAgent(
        schema_validator=validator,
        enable_simulation=True,
        scenario_runner=runner,
    )
    harness = Harness(model_adapter=MockModelAdapter(responses=[valid_outcome_json]))
    context = ExecutionContext(run_id="sim-fallback-001")

    outcome = await agent.execute(payment_problem, harness, context)
    assert "hypothesis" in outcome
    assert outcome["simulation_evidence"]["status"] == "failed"
    assert "error" in outcome["simulation_evidence"]
    validator.validate(outcome, "outcome.schema.json")


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario_file",
    [
        "ad_evaluation.yaml",
        "focus_group.yaml",
        "survey_scenario.yaml",
    ],
)
@pytest.mark.asyncio
async def test_example_simulation_scenarios_execute(scenario_file: str) -> None:
    """All 3 shipped example simulation YAML scenarios execute end-to-end."""
    adapter = TinyTroupeAdapter(max_cost_usd=10.0)
    factory = PersonaFactory(TEMPLATES_PATH, seed=11)
    runner = ScenarioRunner(adapter=adapter, factory=factory)

    result = await runner.run_scenario(REPO_ROOT / "examples" / "simulation" / scenario_file)
    assert result.population_size >= 15
    assert len(result.interactions) == 3
    assert result.aggregated_insights["response_count"] == result.population_size * 3
    assert result.cost_usd < 10.0


@pytest.mark.integration
def test_cli_run_simulation_command() -> None:
    """CLI `pvs run-simulation` executes example scenario and exits 0."""
    scenario = REPO_ROOT / "examples" / "simulation" / "ad_evaluation.yaml"
    exit_code = cli_main(["run-simulation", str(scenario), "--max-cost", "5.0"])
    assert exit_code == 0


@pytest.mark.integration
def test_cli_run_with_enable_simulation_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CLI `pvs run --enable-simulation` completes end-to-end with simulation enabled."""
    monkeypatch.delenv("EMERGENCY_STOP", raising=False)
    monkeypatch.setenv("PVS_DB_PATH", str(tmp_path / "pvs.db"))
    monkeypatch.setenv("PVS_ARTIFACT_ROOT", str(tmp_path / "artifacts"))

    problem_file = REPO_ROOT / "examples" / "greenfield" / "problem_payment_flow.yaml"
    exit_code = cli_main(
        [
            "run",
            str(problem_file),
            "--enable-simulation",
            "--simulation-budget",
            "5.0",
        ]
    )
    assert exit_code == 0


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") or not TINYTROUPE_AVAILABLE,
    reason="Requires OPENAI_API_KEY and installed tinytroupe package",
)
@pytest.mark.asyncio
async def test_real_tinytroupe_simulation() -> None:
    """Test real TinyTroupe simulation when package and OpenAI API key are present."""
    adapter = TinyTroupeAdapter(max_cost_usd=5.0, require_tinytroupe=True)
    factory = PersonaFactory(TEMPLATES_PATH)
    runner = ScenarioRunner(adapter, factory)

    result = await runner.run_scenario(REPO_ROOT / "examples" / "simulation" / "ad_evaluation.yaml")

    assert result.population_size > 0
    assert result.cost_usd < 5.0
    assert len(result.interactions) > 0
    assert result.quality_metrics["persona_adherence"] > 0.5
