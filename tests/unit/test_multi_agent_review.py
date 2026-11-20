"""Unit tests for EVO-013: 4 specialized review agents (Goldfish pattern) and ReviewSynthesizer."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.agents.architecture_review_agent import ArchitectureReviewAgent
from product_value_system.agents.maintainability_review_agent import MaintainabilityReviewAgent
from product_value_system.agents.performance_review_agent import PerformanceReviewAgent
from product_value_system.agents.security_review_agent import SecurityReviewAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.review import (
    FindingCategory,
    FindingSeverity,
    ReviewFinding,
    ReviewSynthesizer,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def validator() -> SchemaValidator:
    return SchemaValidator(REPO_ROOT / "schemas")


@pytest.fixture
def sample_spec() -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "id": "spec-notif-001",
        "version": 1,
        "created": now,
        "outcome_id": "outcome-notif-001",
        "functional_requirements": [
            {
                "id": "FR-001",
                "description": "Accept user payment webhook payload and dispatch push notification",
                "priority": "must-have",
                "acceptance_criteria": ["Dispatches push within 3s"],
                "linked_to": ["primary_metric"],
            }
        ],
        "architecture_decisions": [
            {
                "id": "AD-001",
                "decision": "Queue notifications asynchronously via Redis Streams",
                "rationale": "Decouples payment completion from push gateway latency",
            }
        ],
        "quality_attributes": {
            "performance": {"latency_ms": 200, "throughput_rps": 500},
            "security": ["TLS 1.3", "JWT authentication"],
            "scalability": ["Horizontal worker scaling"],
            "reliability": ["99.9% uptime SLA"],
        },
        "testability_criteria": {
            "unit_tests": ["Validate payload parser"],
            "integration_tests": ["Verify queue dispatch"],
            "acceptance_tests": ["Verify p95 latency < 3s"],
        },
        "constraints": {
            "technical": ["Python 3.12+"],
            "operational": ["Docker container"],
            "business": ["Budget <= $50K"],
        },
    }


def _harness_with_dirty_memory(response: str) -> Harness:
    harness = Harness(model_adapter=MockModelAdapter(responses=[response]))
    harness.memory.add_message("user", "Prior problem context from Elephant stage")
    harness.memory.add_message("assistant", "Prior outcome rationale")
    harness.memory.store_artifact("problem", {"secret_bias": "confirming bias"})
    return harness


# ---------------------------------------------------------------------------
# SecurityReviewAgent (5 tests)
# ---------------------------------------------------------------------------


def test_security_review_prompt_covers_owasp_llm_top_10(validator: SchemaValidator) -> None:
    agent = SecurityReviewAgent(validator)
    prompt = agent._build_system_prompt()
    for code in ("LLM01", "LLM02", "LLM04", "LLM06", "LLM07", "LLM08"):
        assert code in prompt


@pytest.mark.asyncio
async def test_security_review_detects_missing_input_validation(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    resp = json.dumps(
        [
            {
                "severity": "HIGH",
                "category": "security",
                "description": "No input validation specified for webhook payload fields",
                "recommendation": "Add strict schema validation and payload size limit",
                "requirement_id": "FR-001",
                "evidence": "FR-001 accepts arbitrary payload without sanitization",
            }
        ]
    )
    agent = SecurityReviewAgent(validator)
    harness = Harness(model_adapter=MockModelAdapter(responses=[resp]))
    evidence = await agent.execute(sample_spec, harness, ExecutionContext(run_id="s1"))

    validator.validate(evidence, "evidence.schema.json")
    assert evidence["type"] == "security_review"
    assert len(evidence["findings"]) == 1
    assert evidence["findings"][0]["severity"] == "high"
    assert evidence["findings"][0]["references"] == ["FR-001"]


@pytest.mark.asyncio
async def test_security_review_clears_memory_goldfish_pattern(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    resp = json.dumps({"findings": [], "overall_verdict": "approve", "summary": "Clean"})
    harness = _harness_with_dirty_memory(resp)
    assert len(harness.memory.messages) == 2
    assert "problem" in harness.memory.artifacts

    agent = SecurityReviewAgent(validator)
    await agent.execute(sample_spec, harness, ExecutionContext(run_id="s2"))

    # Prior Elephant messages and artifacts must be completely wiped
    assert "problem" not in harness.memory.artifacts
    assert all("Prior problem context" not in m.content for m in harness.memory.messages)


@pytest.mark.asyncio
async def test_security_review_rejects_on_critical_array_finding(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    resp = json.dumps(
        [
            {
                "severity": "CRITICAL",
                "category": "security",
                "description": "Unauthenticated admin endpoint exposes user tokens",
                "recommendation": "Require signed JWT with RBAC policy enforcement",
                "requirement_id": "FR-001",
            }
        ]
    )
    agent = SecurityReviewAgent(validator)
    evidence = await agent.execute(
        sample_spec,
        Harness(model_adapter=MockModelAdapter(responses=[resp])),
        ExecutionContext(run_id="s3"),
    )
    assert evidence["verdict"] == "reject"
    assert evidence["findings"][0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_security_review_handles_fenced_json_response(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    fenced = (
        'Here is my audit:\n```json\n'
        '{"findings": [], "overall_verdict": "approve", "summary": "OK"}\n```'
    )
    agent = SecurityReviewAgent(validator)
    evidence = await agent.execute(
        sample_spec,
        Harness(model_adapter=MockModelAdapter(responses=[fenced])),
        ExecutionContext(run_id="s4"),
    )
    assert evidence["verdict"] == "approve"


# ---------------------------------------------------------------------------
# PerformanceReviewAgent (5 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_performance_review_detects_quadratic_complexity(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    resp = json.dumps(
        [
            {
                "severity": "HIGH",
                "category": "performance",
                "description": "Nested lookup over notifications introduces O(n^2) complexity",
                "recommendation": "Index notifications by user_id hash map for O(1) lookup",
                "requirement_id": "FR-001",
                "estimated_impact": "20x throughput gain at 10K events",
            }
        ]
    )
    agent = PerformanceReviewAgent(validator)
    evidence = await agent.execute(
        sample_spec,
        Harness(model_adapter=MockModelAdapter(responses=[resp])),
        ExecutionContext(run_id="p1"),
    )
    validator.validate(evidence, "evidence.schema.json")
    assert evidence["reviewer"].startswith("performance-review-agent")
    assert evidence["findings"][0]["category"] == "performance"
    assert "o(n^2)" in evidence["findings"][0]["description"].lower()


@pytest.mark.asyncio
async def test_performance_review_identifies_missing_caching(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    resp = json.dumps(
        {
            "findings": [
                {
                    "severity": "medium",
                    "category": "performance",
                    "title": "No device token cache",
                    "description": "Repeated database lookups for device tokens per payment",
                    "recommendation": "Add Redis read-through cache with 5-minute TTL",
                    "references": ["FR-001"],
                }
            ],
            "overall_verdict": "approve_with_concerns",
            "summary": "Cache strategy needed for hot device token lookups",
        }
    )
    agent = PerformanceReviewAgent(validator)
    evidence = await agent.execute(
        sample_spec,
        Harness(model_adapter=MockModelAdapter(responses=[resp])),
        ExecutionContext(run_id="p2"),
    )
    assert evidence["verdict"] == "approve_with_concerns"
    assert "cache" in evidence["findings"][0]["recommendation"].lower()


@pytest.mark.asyncio
async def test_performance_review_flags_unbounded_memory(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    resp = json.dumps(
        [
            {
                "severity": "CRITICAL",
                "category": "performance",
                "description": "Loading full notification history in memory causes OOM",
                "recommendation": "Stream results with cursor pagination (batch size 100)",
                "requirement_id": "FR-001",
            }
        ]
    )
    agent = PerformanceReviewAgent(validator)
    evidence = await agent.execute(
        sample_spec,
        Harness(model_adapter=MockModelAdapter(responses=[resp])),
        ExecutionContext(run_id="p3"),
    )
    assert evidence["verdict"] == "reject"
    assert evidence["findings"][0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_performance_review_clears_memory(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    resp = json.dumps([])
    harness = _harness_with_dirty_memory(resp)
    agent = PerformanceReviewAgent(validator)
    await agent.execute(sample_spec, harness, ExecutionContext(run_id="p4"))
    assert "problem" not in harness.memory.artifacts
    assert all("Prior problem context" not in m.content for m in harness.memory.messages)


def test_performance_review_prompt_includes_key_dimensions(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    agent = PerformanceReviewAgent(validator)
    sys_prompt = agent._build_system_prompt().lower()
    user_prompt = agent._build_review_prompt(sample_spec)
    assert "complexity" in sys_prompt and "caching" in sys_prompt and "latency" in sys_prompt
    assert "spec-notif-001" in user_prompt


# ---------------------------------------------------------------------------
# MaintainabilityReviewAgent (4 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintainability_review_detects_missing_acceptance_criteria(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    resp = json.dumps(
        [
            {
                "severity": "MEDIUM",
                "category": "maintainability",
                "description": (
                    "FR-001 lacks negative-path acceptance criteria for gateway timeouts"
                ),
                "recommendation": "Add measurable retry and DLQ acceptance criteria",
                "requirement_id": "FR-001",
            }
        ]
    )
    agent = MaintainabilityReviewAgent(validator)
    evidence = await agent.execute(
        sample_spec,
        Harness(model_adapter=MockModelAdapter(responses=[resp])),
        ExecutionContext(run_id="m1"),
    )
    validator.validate(evidence, "evidence.schema.json")
    assert evidence["reviewer"].startswith("maintainability-review-agent")
    assert evidence["findings"][0]["category"] == "maintainability"


@pytest.mark.asyncio
async def test_maintainability_review_flags_low_test_coverage(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    resp = json.dumps(
        [
            {
                "severity": "MEDIUM",
                "category": "maintainability",
                "description": "Testability criteria omit quantitative line coverage target",
                "recommendation": "Specify >=85% unit and integration test coverage gate",
                "requirement_id": "FR-001",
            }
        ]
    )
    agent = MaintainabilityReviewAgent(validator)
    evidence = await agent.execute(
        sample_spec,
        Harness(model_adapter=MockModelAdapter(responses=[resp])),
        ExecutionContext(run_id="m2"),
    )
    assert "coverage" in evidence["findings"][0]["description"].lower()


@pytest.mark.asyncio
async def test_maintainability_review_identifies_clarity_gap(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    resp = json.dumps(
        [
            {
                "severity": "LOW",
                "category": "maintainability",
                "description": "Ambiguous naming between 'dispatch' and 'send' across interfaces",
                "recommendation": "Standardize terminology in ubiquitous domain glossary",
                "requirement_id": "FR-001",
            }
        ]
    )
    agent = MaintainabilityReviewAgent(validator)
    evidence = await agent.execute(
        sample_spec,
        Harness(model_adapter=MockModelAdapter(responses=[resp])),
        ExecutionContext(run_id="m3"),
    )
    assert evidence["verdict"] == "approve"
    assert evidence["findings"][0]["severity"] == "low"


@pytest.mark.asyncio
async def test_maintainability_review_clears_memory(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    harness = _harness_with_dirty_memory(json.dumps([]))
    agent = MaintainabilityReviewAgent(validator)
    await agent.execute(sample_spec, harness, ExecutionContext(run_id="m4"))
    assert "problem" not in harness.memory.artifacts
    assert all("Prior problem context" not in m.content for m in harness.memory.messages)


# ---------------------------------------------------------------------------
# ArchitectureReviewAgent enhancements (2 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_architecture_review_accepts_array_and_clears_memory(
    validator: SchemaValidator,
    sample_spec: dict[str, Any],
) -> None:
    resp = json.dumps(
        [
            {
                "severity": "HIGH",
                "category": "architecture",
                "description": (
                    "Single Redis stream without consumer group partitioning creates SPOF"
                ),
                "recommendation": (
                    "Partition stream by merchant_id with replicated sentinel cluster"
                ),
                "requirement_id": "AD-001",
            }
        ]
    )
    harness = _harness_with_dirty_memory(resp)
    agent = ArchitectureReviewAgent(validator)
    evidence = await agent.execute(sample_spec, harness, ExecutionContext(run_id="a1"))

    validator.validate(evidence, "evidence.schema.json")
    assert evidence["type"] == "architecture_review"
    assert evidence["findings"][0]["references"] == ["AD-001"]
    assert "problem" not in harness.memory.artifacts


def test_finding_models_severity_rank_and_category_aliases() -> None:
    assert FindingSeverity.CRITICAL.rank > FindingSeverity.HIGH.rank
    assert FindingSeverity.HIGH.rank > FindingSeverity.MEDIUM.rank
    assert FindingSeverity.MEDIUM.rank > FindingSeverity.LOW.rank
    assert FindingCategory.from_raw("authentication") == FindingCategory.SECURITY
    assert FindingCategory.from_raw("latency") == FindingCategory.PERFORMANCE
    assert FindingCategory.from_raw("testability") == FindingCategory.MAINTAINABILITY


# ---------------------------------------------------------------------------
# ReviewSynthesizer (6 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesizer_aggregates_and_prioritizes_all_4_reviewers() -> None:
    synthesizer = ReviewSynthesizer()
    result = await synthesizer.synthesize(
        security_result={
            "reviewer": "security-review-agent",
            "artifact_id": "spec-001",
            "findings": [
                {
                    "severity": "HIGH",
                    "category": "security",
                    "description": "Add rate limiting",
                    "recommendation": "Enforce 100 RPS per tenant",
                    "requirement_id": "FR-001",
                }
            ],
        },
        performance_result={
            "reviewer": "performance-review-agent",
            "findings": [
                {
                    "severity": "MEDIUM",
                    "category": "performance",
                    "description": "Add DB index",
                    "recommendation": "Create composite index on (tenant_id, created_at)",
                    "requirement_id": "FR-002",
                }
            ],
        },
        maintainability_result={
            "reviewer": "maintainability-review-agent",
            "findings": [
                {
                    "severity": "LOW",
                    "category": "maintainability",
                    "description": "Clarify docstring",
                    "recommendation": "Document error codes",
                    "requirement_id": "FR-003",
                }
            ],
        },
        architecture_result={
            "reviewer": "architecture-review-agent",
            "findings": [
                {
                    "severity": "HIGH",
                    "category": "architecture",
                    "description": "Missing idempotency key",
                    "recommendation": "Require Idempotency-Key header",
                    "requirement_id": "FR-004",
                }
            ],
        },
    )

    assert result.specification_id == "spec-001"
    assert len(result.findings) == 4
    assert result.summary == {"CRITICAL": 0, "HIGH": 2, "MEDIUM": 1, "LOW": 1}
    assert result.findings[0].severity == FindingSeverity.HIGH
    assert result.findings[0].category == FindingCategory.SECURITY
    assert result.findings[-1].severity == FindingSeverity.LOW
    assert result.gate_decision == "PASS"
    assert result.verdict == "APPROVED_WITH_CONCERNS"


@pytest.mark.asyncio
async def test_synthesizer_resolves_security_vs_performance_conflict() -> None:
    synthesizer = ReviewSynthesizer()
    result = await synthesizer.synthesize(
        security_result={
            "reviewer": "security-review-agent",
            "findings": [
                {
                    "severity": "HIGH",
                    "category": "security",
                    "description": "Sensitive payload transmitted unencrypted",
                    "recommendation": "Encrypt payload with AES-256 before caching",
                    "requirement_id": "FR-001",
                }
            ],
        },
        performance_result={
            "reviewer": "performance-review-agent",
            "findings": [
                {
                    "severity": "HIGH",
                    "category": "performance",
                    "description": "Encryption overhead adds 8ms",
                    "recommendation": "Skip encryption to reduce p95 latency",
                    "requirement_id": "FR-001",
                }
            ],
        },
        maintainability_result={"reviewer": "maintainability-review-agent", "findings": []},
        architecture_result={"reviewer": "architecture-review-agent", "findings": []},
    )

    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.conflict_type == "security_vs_performance"
    assert "security-review-agent" in conflict.resolution
    assert "encrypt" in conflict.chosen_recommendation.lower()


@pytest.mark.asyncio
async def test_synthesizer_resolves_performance_vs_maintainability_and_severity_override() -> None:
    synthesizer = ReviewSynthesizer()
    # Equal severity: Performance beats Maintainability
    res_equal = await synthesizer.synthesize(
        security_result={"reviewer": "security-review-agent", "findings": []},
        performance_result={
            "reviewer": "performance-review-agent",
            "findings": [
                {
                    "severity": "MEDIUM",
                    "category": "performance",
                    "description": "Hot path allocates repeatedly",
                    "recommendation": "Denormalize lookup cache for hot path",
                    "requirement_id": "FR-002",
                }
            ],
        },
        maintainability_result={
            "reviewer": "maintainability-review-agent",
            "findings": [
                {
                    "severity": "MEDIUM",
                    "category": "maintainability",
                    "description": "Complex schema duplication",
                    "recommendation": "Normalize schema and simplify module",
                    "requirement_id": "FR-002",
                }
            ],
        },
        architecture_result={"reviewer": "architecture-review-agent", "findings": []},
    )
    assert len(res_equal.conflicts) == 1
    assert res_equal.conflicts[0].conflict_type == "performance_vs_maintainability"
    assert "performance-review-agent" in res_equal.conflicts[0].resolution

    # Higher severity overrides category precedence
    res_sev = await synthesizer.synthesize(
        security_result={"reviewer": "security-review-agent", "findings": []},
        performance_result={
            "reviewer": "performance-review-agent",
            "findings": [
                {
                    "severity": "LOW",
                    "category": "performance",
                    "description": "Minor overhead",
                    "recommendation": "Inline helper function",
                    "requirement_id": "FR-002",
                }
            ],
        },
        maintainability_result={
            "reviewer": "maintainability-review-agent",
            "findings": [
                {
                    "severity": "HIGH",
                    "category": "maintainability",
                    "description": "Untestable monolithic block",
                    "recommendation": "Extract and modularize testable helper",
                    "requirement_id": "FR-002",
                }
            ],
        },
        architecture_result={"reviewer": "architecture-review-agent", "findings": []},
    )
    assert len(res_sev.conflicts) == 1
    assert "maintainability-review-agent" in res_sev.conflicts[0].resolution
    assert "higher severity" in res_sev.conflicts[0].rationale.lower()


@pytest.mark.asyncio
async def test_synthesizer_fails_gate_on_critical_finding() -> None:
    synthesizer = ReviewSynthesizer()
    result = await synthesizer.synthesize(
        security_result={
            "reviewer": "security-review-agent",
            "findings": [
                ReviewFinding(
                    id="sec-crit-1",
                    reviewer="security-review-agent",
                    severity=FindingSeverity.CRITICAL,
                    category=FindingCategory.SECURITY,
                    description="Raw SQL concatenation allows SQL injection",
                    recommendation="Use parameterized SQL queries",
                    requirement_id="FR-001",
                )
            ],
        },
        performance_result={"reviewer": "performance-review-agent", "findings": []},
        maintainability_result={"reviewer": "maintainability-review-agent", "findings": []},
        architecture_result={"reviewer": "architecture-review-agent", "findings": []},
    )
    assert result.gate_decision == "FAIL"
    assert result.verdict == "REJECTED"
    assert result.summary["CRITICAL"] == 1


@pytest.mark.asyncio
async def test_synthesizer_fails_gate_when_high_findings_exceed_threshold() -> None:
    synthesizer = ReviewSynthesizer(max_high_findings=5)
    six_highs = [
        {
            "severity": "HIGH",
            "category": "performance",
            "description": f"Bottleneck #{i}",
            "recommendation": f"Fix bottleneck #{i}",
            "requirement_id": f"FR-00{i}",
        }
        for i in range(1, 7)
    ]
    result = await synthesizer.synthesize(
        security_result={"reviewer": "security-review-agent", "findings": []},
        performance_result={"reviewer": "performance-review-agent", "findings": six_highs},
        maintainability_result={"reviewer": "maintainability-review-agent", "findings": []},
        architecture_result={"reviewer": "architecture-review-agent", "findings": []},
    )
    assert result.gate_decision == "FAIL"
    assert result.summary["HIGH"] == 6
    assert "exceed threshold" in result.gate_reason.lower()


@pytest.mark.asyncio
async def test_synthesizer_passes_on_low_findings_and_exports_valid_evidence(
    validator: SchemaValidator,
) -> None:
    synthesizer = ReviewSynthesizer()
    result = await synthesizer.synthesize(
        security_result={"reviewer": "security-review-agent", "findings": []},
        performance_result={"reviewer": "performance-review-agent", "findings": []},
        maintainability_result={
            "reviewer": "maintainability-review-agent",
            "findings": [
                {
                    "severity": "LOW",
                    "category": "maintainability",
                    "description": "Minor naming suggestion",
                    "recommendation": "Use consistent snake_case",
                    "requirement_id": "FR-001",
                }
            ],
        },
        architecture_result={"reviewer": "architecture-review-agent", "findings": []},
        specification_id="spec-clean-001",
    )
    assert result.gate_decision == "PASS"
    assert result.verdict == "APPROVED"
    evidence = result.to_evidence_artifact()
    validator.validate(evidence, "evidence.schema.json")
