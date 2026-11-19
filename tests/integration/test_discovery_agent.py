"""
Discovery agent integration tests (P009).
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.agents.discovery_agent import DiscoveryAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness


@pytest.fixture
def schema_validator() -> SchemaValidator:
    """Schema validator."""
    schema_dir = Path(__file__).parent.parent.parent / "schemas"
    return SchemaValidator(schema_dir)


@pytest.fixture
def mock_outcome_response() -> str:
    """Mock LLM response with valid outcome."""
    return json.dumps(
        {
            "hypothesis": (
                "Reducing payment fees from 3.2% to 2.5% will increase merchant "
                "retention by 15% within 6 months"
            ),
            "success_criteria": {
                "primary_metric": {
                    "name": "Merchant retention rate",
                    "unit": "percentage",
                    "measurement_method": "Active merchants month N / Active merchants month N-1",
                },
                "baseline": 85.0,
                "target": 97.75,
                "guardrails": [
                    {
                        "metric": "Transaction volume",
                        "threshold": 1000000,
                        "direction": "above",
                    },
                    {
                        "metric": "Revenue per merchant",
                        "threshold": 500,
                        "direction": "above",
                    },
                ],
            },
            "evidence_requirements": [
                {
                    "type": "quantitative",
                    "description": "Survey 100 merchants on fee sensitivity",
                    "mandatory": True,
                },
                {
                    "type": "quantitative",
                    "description": "Analyze churn correlation with fees",
                    "mandatory": True,
                },
                {
                    "type": "qualitative",
                    "description": "Competitor fee analysis",
                    "mandatory": False,
                },
            ],
        }
    )


@pytest.fixture
def sample_problem() -> dict[str, Any]:
    """Sample problem statement."""
    return {
        "id": "problem-payment-fees-001",
        "version": 1,
        "created": datetime.now(UTC).isoformat(),
        "statement": "Small merchants struggle with high payment processing fees",
        "context": {
            "user_segment": "Small merchants (1-10 employees)",
            "business_context": "Strategic initiative to increase SMB market share by 15%",
        },
        "constraints": {
            "no_pii": True,
            "synthetic_only": True,
            "max_cost_usd": 50.0,
        },
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_discovery_agent_produces_valid_outcome(
    schema_validator: SchemaValidator,
    sample_problem: dict[str, Any],
    mock_outcome_response: str,
) -> None:
    """Test discovery agent produces schema-valid outcome."""
    agent = DiscoveryAgent(schema_validator)

    model_adapter = MockModelAdapter(responses=[mock_outcome_response])
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-001")

    outcome = await agent.execute(sample_problem, harness, context)

    assert outcome is not None
    assert outcome["id"] == "outcome-payment-fees-001"
    assert outcome["problem_id"] == "problem-payment-fees-001"
    assert "hypothesis" in outcome
    assert "success_criteria" in outcome

    schema_validator.validate(outcome, "outcome.schema.json")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_discovery_agent_links_to_problem(
    schema_validator: SchemaValidator,
    sample_problem: dict[str, Any],
    mock_outcome_response: str,
) -> None:
    """Test outcome correctly references problem."""
    agent = DiscoveryAgent(schema_validator)

    model_adapter = MockModelAdapter(responses=[mock_outcome_response])
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-002")

    outcome = await agent.execute(sample_problem, harness, context)

    assert outcome["problem_id"] == sample_problem["id"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_discovery_agent_includes_success_criteria(
    schema_validator: SchemaValidator,
    sample_problem: dict[str, Any],
    mock_outcome_response: str,
) -> None:
    """Test outcome includes measurable success criteria."""
    agent = DiscoveryAgent(schema_validator)

    model_adapter = MockModelAdapter(responses=[mock_outcome_response])
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-003")

    outcome = await agent.execute(sample_problem, harness, context)

    criteria = outcome["success_criteria"]
    assert "primary_metric" in criteria
    assert "baseline" in criteria
    assert "target" in criteria
    assert criteria["baseline"] < criteria["target"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_discovery_agent_rejects_invalid_problem(
    schema_validator: SchemaValidator,
) -> None:
    """Test agent rejects invalid problem input."""
    agent = DiscoveryAgent(schema_validator)

    invalid_problem = {"id": "bad", "version": 1}

    harness = Harness(model_adapter=MockModelAdapter())
    context = ExecutionContext(run_id="test-run-004")

    with pytest.raises(jsonschema.ValidationError):
        await agent.execute(invalid_problem, harness, context)
