"""
Review agents integration tests (P011).
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.agents.architecture_review_agent import ArchitectureReviewAgent
from product_value_system.agents.security_review_agent import SecurityReviewAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness


@pytest.fixture
def schema_validator() -> SchemaValidator:
    """Schema validator."""
    schema_dir = Path(__file__).parent.parent.parent / "schemas"
    return SchemaValidator(schema_dir)


@pytest.fixture
def sample_specification() -> dict[str, Any]:
    """Sample specification for review."""
    now = datetime.now(UTC).isoformat()
    return {
        "id": "spec-payment-fees-001",
        "version": 1,
        "created": now,
        "updated": now,
        "outcome_id": "outcome-payment-fees-001",
        "functional_requirements": [
            {
                "id": "FR-001",
                "description": "System shall process payments with new fee structure",
                "priority": "must-have",
                "acceptance_criteria": ["Fee calculation updated", "Tests pass"],
                "linked_to": ["primary_metric"],
            }
        ],
        "architecture_decisions": [
            {
                "id": "AD-001",
                "decision": "Use event-driven architecture",
                "rationale": "Decouples components",
                "alternatives": ["Monolithic"],
                "trade_offs": "Complexity vs flexibility",
            }
        ],
        "quality_attributes": {
            "performance": {"latency_ms": 200},
            "security": ["Authentication required"],
            "scalability": ["Horizontal scaling"],
            "reliability": ["99.9% uptime"],
        },
        "testability_criteria": {
            "unit_tests": ["Test fee calculation"],
            "integration_tests": ["Test payment flow"],
            "acceptance_tests": ["Verify metrics"],
        },
        "constraints": {
            "technical": ["Python 3.12+"],
            "operational": ["Docker deployment"],
            "business": ["Max $40K cost"],
        },
    }


@pytest.fixture
def mock_architecture_review_clean() -> str:
    """Mock architecture review with no findings."""
    return json.dumps(
        {
            "findings": [],
            "overall_verdict": "approve",
            "summary": "Specification is technically sound with no major concerns",
        }
    )


@pytest.fixture
def mock_architecture_review_with_findings() -> str:
    """Mock architecture review with findings."""
    return json.dumps(
        {
            "findings": [
                {
                    "severity": "high",
                    "category": "scalability",
                    "title": "Horizontal scaling not detailed",
                    "description": (
                        "FR-001 mentions horizontal scaling but AD-001 doesn't "
                        "specify how components scale"
                    ),
                    "recommendation": "Add architecture decision detailing scaling strategy",
                    "references": ["FR-001", "AD-001"],
                }
            ],
            "overall_verdict": "approve_with_concerns",
            "summary": "Specification is sound but has scalability detail gaps",
        }
    )


@pytest.fixture
def mock_security_review_with_findings() -> str:
    """Mock security review with findings."""
    return json.dumps(
        {
            "findings": [
                {
                    "severity": "critical",
                    "category": "authentication",
                    "title": "Missing authentication requirement",
                    "description": (
                        "FR-001 does not specify authentication for payment "
                        "processing. Attack scenario: unauthenticated payment modification"
                    ),
                    "recommendation": (
                        "Add requirement: 'All payment endpoints require JWT "
                        "authentication with user scope validation'"
                    ),
                    "references": ["FR-001"],
                }
            ],
            "overall_verdict": "reject",
            "summary": "Critical authentication gap identified",
        }
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_architecture_review_produces_valid_evidence(
    schema_validator: SchemaValidator,
    sample_specification: dict[str, Any],
    mock_architecture_review_clean: str,
) -> None:
    """Test architecture review agent produces schema-valid evidence."""
    agent = ArchitectureReviewAgent(schema_validator)

    model_adapter = MockModelAdapter(responses=[mock_architecture_review_clean])
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-001")

    evidence = await agent.execute(sample_specification, harness, context)

    assert evidence is not None
    assert evidence["id"] == "spec-payment-fees-001-architecture-review"
    assert evidence["artifact_id"] == "spec-payment-fees-001"
    assert evidence["type"] == "architecture_review"

    schema_validator.validate(evidence, "evidence.schema.json")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_architecture_review_identifies_issues(
    schema_validator: SchemaValidator,
    sample_specification: dict[str, Any],
    mock_architecture_review_with_findings: str,
) -> None:
    """Test architecture review identifies technical issues."""
    agent = ArchitectureReviewAgent(schema_validator)

    model_adapter = MockModelAdapter(responses=[mock_architecture_review_with_findings])
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-002")

    evidence = await agent.execute(sample_specification, harness, context)

    assert len(evidence["findings"]) > 0
    finding = evidence["findings"][0]
    assert "severity" in finding
    assert "category" in finding
    assert "recommendation" in finding
    assert evidence["verdict"] == "approve_with_concerns"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_security_review_produces_valid_evidence(
    schema_validator: SchemaValidator,
    sample_specification: dict[str, Any],
    mock_security_review_with_findings: str,
) -> None:
    """Test security review agent produces schema-valid evidence."""
    agent = SecurityReviewAgent(schema_validator)

    model_adapter = MockModelAdapter(responses=[mock_security_review_with_findings])
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-003")

    evidence = await agent.execute(sample_specification, harness, context)

    assert evidence is not None
    assert evidence["id"] == "spec-payment-fees-001-security-review"
    assert evidence["type"] == "security_review"

    schema_validator.validate(evidence, "evidence.schema.json")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_security_review_identifies_critical_risks(
    schema_validator: SchemaValidator,
    sample_specification: dict[str, Any],
    mock_security_review_with_findings: str,
) -> None:
    """Test security review identifies critical security risks."""
    agent = SecurityReviewAgent(schema_validator)

    model_adapter = MockModelAdapter(responses=[mock_security_review_with_findings])
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-004")

    evidence = await agent.execute(sample_specification, harness, context)

    assert len(evidence["findings"]) > 0
    critical_findings = [f for f in evidence["findings"] if f["severity"] == "critical"]
    assert len(critical_findings) > 0
    assert evidence["verdict"] == "reject"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_review_agents_operate_independently(
    schema_validator: SchemaValidator,
    sample_specification: dict[str, Any],
    mock_architecture_review_clean: str,
    mock_security_review_with_findings: str,
) -> None:
    """Test architecture and security reviews are independent."""
    arch_agent = ArchitectureReviewAgent(schema_validator)
    sec_agent = SecurityReviewAgent(schema_validator)

    arch_harness = Harness(
        model_adapter=MockModelAdapter(responses=[mock_architecture_review_clean])
    )
    sec_harness = Harness(
        model_adapter=MockModelAdapter(responses=[mock_security_review_with_findings])
    )

    context = ExecutionContext(run_id="test-run-005")

    arch_evidence = await arch_agent.execute(sample_specification, arch_harness, context)
    sec_evidence = await sec_agent.execute(sample_specification, sec_harness, context)

    assert arch_evidence["verdict"] == "approve"
    assert sec_evidence["verdict"] == "reject"

    assert arch_evidence["type"] == "architecture_review"
    assert sec_evidence["type"] == "security_review"
