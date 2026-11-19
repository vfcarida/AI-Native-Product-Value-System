"""
Evaluation framework tests (P013).
"""

import json
from pathlib import Path

import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.evaluation.ai_judges import ClarityJudge, CompletenessJudge
from product_value_system.evaluation.deterministic_judges import (
    PythonSyntaxJudge,
    SchemaValidationJudge,
    TestCoverageJudge,
)
from product_value_system.evaluation.golden_dataset import (
    GoldenDatasetLoader,
    GoldenSample,
)
from product_value_system.evaluation.pipeline import EvaluationPipeline


@pytest.fixture
def schema_validator() -> SchemaValidator:
    """Schema validator."""
    schema_dir = Path(__file__).parent.parent.parent / "schemas"
    return SchemaValidator(schema_dir)


@pytest.mark.asyncio
async def test_schema_validation_judge_passes_valid_artifact(
    schema_validator: SchemaValidator,
) -> None:
    """Test schema validation judge accepts valid artifact."""
    judge = SchemaValidationJudge(schema_validator)

    artifact = {
        "id": "problem-test-001",
        "version": 1,
        "created": "2026-11-18T10:00:00Z",
        "statement": "Test problem statement that exceeds minimum length requirement.",
        "context": {"user_segment": "test", "business_context": "test"},
        "constraints": {"synthetic_only": True},
    }

    result = await judge.evaluate(artifact, context={"artifact_type": "problem"})

    assert result.passed is True
    assert result.score == 1.0


@pytest.mark.asyncio
async def test_schema_validation_judge_fails_invalid_artifact(
    schema_validator: SchemaValidator,
) -> None:
    """Test schema validation judge rejects invalid artifact."""
    judge = SchemaValidationJudge(schema_validator)

    artifact = {"id": "bad"}

    result = await judge.evaluate(artifact, context={"artifact_type": "problem"})

    assert result.passed is False
    assert result.score == 0.0


@pytest.mark.asyncio
async def test_python_syntax_judge() -> None:
    """Test Python syntax judge."""
    judge = PythonSyntaxJudge()

    valid_result = await judge.evaluate({"code": "def add(a, b): return a + b"})
    assert valid_result.passed is True

    invalid_result = await judge.evaluate({"code": "def add(a, b: return a + b"})
    assert invalid_result.passed is False


@pytest.mark.asyncio
async def test_test_coverage_judge() -> None:
    """Test coverage judge."""
    judge = TestCoverageJudge(min_coverage=0.8)

    high_coverage = await judge.evaluate({"coverage_percent": 85.0})
    assert high_coverage.passed is True

    low_coverage = await judge.evaluate({"coverage_percent": 50.0})
    assert low_coverage.passed is False


@pytest.mark.asyncio
async def test_completeness_and_clarity_judges() -> None:
    """Test AI-based completeness and clarity judges."""
    mock_adapter = MockModelAdapter(
        responses=[
            json.dumps({"score": 0.85, "rationale": "Mostly complete"}),
            json.dumps({"score": 0.90, "rationale": "Clear and unambiguous"}),
        ]
    )

    completeness_judge = CompletenessJudge(mock_adapter)
    clarity_judge = ClarityJudge(mock_adapter)

    artifact = {
        "id": "spec-001",
        "functional_requirements": ["FR-001"],
        "architecture_decisions": [],
    }

    c_result = await completeness_judge.evaluate(artifact)
    assert c_result.score == 0.85
    assert c_result.passed is True

    cl_result = await clarity_judge.evaluate(artifact)
    assert cl_result.score == 0.90
    assert cl_result.passed is True


@pytest.mark.asyncio
async def test_evaluation_pipeline_runs_multiple_judges_and_produces_evidence(
    schema_validator: SchemaValidator,
) -> None:
    """Test evaluation pipeline runs judges in parallel and produces valid evidence."""
    judges = [
        SchemaValidationJudge(schema_validator),
        PythonSyntaxJudge(),
    ]

    pipeline = EvaluationPipeline(judges, passing_threshold=0.7)

    artifact = {
        "id": "problem-test-001",
        "version": 1,
        "created": "2026-11-18T10:00:00Z",
        "statement": "Test problem statement that exceeds minimum length requirement.",
        "context": {"user_segment": "test", "business_context": "test"},
        "constraints": {"synthetic_only": True},
        "code": "def test(): pass",
    }

    result = await pipeline.evaluate(
        artifact,
        context={"artifact_type": "problem"},
    )

    assert len(result.judge_results) == 2
    assert result.overall_score == 1.0
    assert result.passed is True
    assert result.failed_judges == []

    evidence = result.to_evidence_artifact()
    schema_validator.validate(evidence, "evidence.schema.json")


def test_golden_dataset_loader(tmp_path: Path) -> None:
    """Test golden dataset loader."""
    loader = GoldenDatasetLoader(tmp_path)

    sample = GoldenSample(
        id="sample-001",
        input_artifact={"problem": "test"},
        expected_output={"outcome": "expected"},
    )

    loader.save_sample("test_dataset", sample)

    loaded_samples = loader.load_dataset("test_dataset")

    assert len(loaded_samples) == 1
    assert loaded_samples[0].id == "sample-001"
