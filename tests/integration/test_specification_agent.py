"""
Specification agent integration tests (P010).
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.agents.specification_agent import SpecificationAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness


@pytest.fixture
def schema_validator() -> SchemaValidator:
    """Schema validator."""
    schema_dir = Path(__file__).parent.parent.parent / "schemas"
    return SchemaValidator(schema_dir)


@pytest.fixture
def mock_specification_response() -> str:
    """Mock LLM response with valid specification."""
    return json.dumps(
        {
            "functional_requirements": [
                {
                    "id": "FR-001",
                    "description": (
                        "System shall reduce payment processing fees from 3.2% to 2.5%"
                    ),
                    "priority": "must-have",
                    "acceptance_criteria": [
                        "Fee calculation updated to 2.5%",
                        "Fee displayed correctly on payment screen",
                        "Merchant statements show new fee rate",
                    ],
                    "linked_to": ["primary_metric"],
                },
                {
                    "id": "FR-002",
                    "description": "System shall track merchant retention rate monthly",
                    "priority": "must-have",
                    "acceptance_criteria": [
                        "Active merchant count calculated monthly",
                        "Retention rate computed as (month N / month N-1)",
                        "Dashboard displays retention trend",
                    ],
                    "linked_to": ["primary_metric"],
                },
            ],
            "architecture_decisions": [
                {
                    "id": "AD-001",
                    "decision": "Use event-driven architecture for fee calculation updates",
                    "rationale": (
                        "Enables audit trail and decouples pricing from payment processing"
                    ),
                    "alternatives": [
                        "Direct database updates",
                        "Scheduled batch processing",
                    ],
                    "trade_offs": (
                        "Increased complexity for better auditability and flexibility"
                    ),
                }
            ],
            "quality_attributes": {
                "performance": {
                    "latency_ms": 200,
                    "throughput_rps": 500,
                },
                "security": [
                    "PCI DSS compliance maintained",
                    "Fee changes logged with audit trail",
                ],
                "scalability": [
                    "Support 100K merchants",
                    "Horizontal scaling for calculation service",
                ],
                "reliability": [
                    "99.9% uptime target",
                    "Graceful degradation if calculation service unavailable",
                ],
            },
            "testability_criteria": {
                "unit_tests": [
                    "Test fee calculation logic with various amounts",
                    "Test retention rate calculation with edge cases",
                ],
                "integration_tests": [
                    "Test end-to-end payment flow with new fee",
                    "Test merchant statement generation",
                ],
                "acceptance_tests": [
                    "Verify merchant retention increases by 15%",
                    "Verify no regression in transaction volume",
                ],
            },
            "constraints": {
                "technical": [
                    "Python 3.12+",
                    "PostgreSQL database",
                    "Docker containers",
                ],
                "operational": [
                    "Zero-downtime deployment required",
                    "Rollback capability",
                ],
                "business": [
                    "Implementation within 6 weeks",
                    "Max $40K development cost",
                ],
            },
        }
    )


@pytest.fixture
def sample_outcome() -> dict[str, Any]:
    """Sample outcome hypothesis."""
    now = datetime.now(UTC).isoformat()
    return {
        "id": "outcome-payment-fees-001",
        "version": 1,
        "created": now,
        "updated": now,
        "problem_id": "problem-payment-fees-001",
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
                "description": "Survey 100 merchants",
                "mandatory": True,
            }
        ],
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_specification_agent_produces_valid_spec(
    schema_validator: SchemaValidator,
    sample_outcome: dict[str, Any],
    mock_specification_response: str,
) -> None:
    """Test specification agent produces schema-valid specification."""
    agent = SpecificationAgent(schema_validator)

    model_adapter = MockModelAdapter(responses=[mock_specification_response])
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-001")

    specification = await agent.execute(sample_outcome, harness, context)

    assert specification is not None
    assert specification["id"] == "spec-payment-fees-001"
    assert specification["outcome_id"] == "outcome-payment-fees-001"
    assert "functional_requirements" in specification
    assert "architecture_decisions" in specification

    schema_validator.validate(specification, "specification.schema.json")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_specification_agent_links_to_outcome(
    schema_validator: SchemaValidator,
    sample_outcome: dict[str, Any],
    mock_specification_response: str,
) -> None:
    """Test specification correctly references outcome."""
    agent = SpecificationAgent(schema_validator)

    model_adapter = MockModelAdapter(responses=[mock_specification_response])
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-002")

    specification = await agent.execute(sample_outcome, harness, context)

    assert specification["outcome_id"] == sample_outcome["id"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_specification_includes_testability_criteria(
    schema_validator: SchemaValidator,
    sample_outcome: dict[str, Any],
    mock_specification_response: str,
) -> None:
    """Test specification includes comprehensive testability criteria."""
    agent = SpecificationAgent(schema_validator)

    model_adapter = MockModelAdapter(responses=[mock_specification_response])
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-003")

    specification = await agent.execute(sample_outcome, harness, context)

    testability = specification["testability_criteria"]
    assert "unit_tests" in testability
    assert "integration_tests" in testability
    assert "acceptance_tests" in testability
    assert len(testability["unit_tests"]) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_specification_includes_quality_attributes(
    schema_validator: SchemaValidator,
    sample_outcome: dict[str, Any],
    mock_specification_response: str,
) -> None:
    """Test specification defines quality attributes."""
    agent = SpecificationAgent(schema_validator)

    model_adapter = MockModelAdapter(responses=[mock_specification_response])
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-004")

    specification = await agent.execute(sample_outcome, harness, context)

    quality = specification["quality_attributes"]
    assert "performance" in quality
    assert "security" in quality
    assert "scalability" in quality
    assert "reliability" in quality


@pytest.mark.integration
@pytest.mark.asyncio
async def test_specification_agent_rejects_invalid_outcome(
    schema_validator: SchemaValidator,
) -> None:
    """Test agent rejects invalid outcome input."""
    agent = SpecificationAgent(schema_validator)

    invalid_outcome = {"id": "bad", "version": 1}

    harness = Harness(model_adapter=MockModelAdapter())
    context = ExecutionContext(run_id="test-run-005")

    with pytest.raises(jsonschema.ValidationError):
        await agent.execute(invalid_outcome, harness, context)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_functional_requirements_have_acceptance_criteria(
    schema_validator: SchemaValidator,
    sample_outcome: dict[str, Any],
    mock_specification_response: str,
) -> None:
    """Test all functional requirements include acceptance criteria."""
    agent = SpecificationAgent(schema_validator)

    model_adapter = MockModelAdapter(responses=[mock_specification_response])
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-006")

    specification = await agent.execute(sample_outcome, harness, context)

    requirements = specification["functional_requirements"]
    assert len(requirements) > 0

    for req in requirements:
        assert "acceptance_criteria" in req
        assert len(req["acceptance_criteria"]) > 0
