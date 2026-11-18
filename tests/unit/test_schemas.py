"""
Schema validation tests for P002 Contracts and Schemas.
"""

import json
from pathlib import Path

import jsonschema
import pytest

from product_value_system.contracts.validation import SchemaValidator


@pytest.fixture
def schema_dir() -> Path:
    """Path to schema directory."""
    return Path(__file__).parent.parent.parent / "schemas"


@pytest.fixture
def validator(schema_dir: Path) -> SchemaValidator:
    """Schema validator instance."""
    return SchemaValidator(schema_dir)


@pytest.fixture
def examples_dir(schema_dir: Path) -> Path:
    """Path to examples directory."""
    return schema_dir / "examples"


@pytest.mark.unit
def test_all_schemas_load(validator: SchemaValidator) -> None:
    """Test that all 6 schemas load without errors and use JSON Schema 2020-12."""
    assert len(validator._schemas) >= 6

    expected = [
        "problem.schema.json",
        "outcome.schema.json",
        "specification.schema.json",
        "evidence.schema.json",
        "agent_definition.schema.json",
        "event_envelope.schema.json",
    ]

    for schema_id in expected:
        assert schema_id in validator._schemas, f"Missing schema: {schema_id}"
        schema = validator._schemas[schema_id]
        assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
        assert schema.get("$id", "").startswith("https://pvs.ai/schemas/")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("example_filename", "schema_id"),
    [
        ("problem_example.json", "problem.schema.json"),
        ("outcome_example.json", "outcome.schema.json"),
        ("specification_example.json", "specification.schema.json"),
        ("evidence_example.json", "evidence.schema.json"),
        ("agent_definition_example.json", "agent_definition.schema.json"),
        ("event_envelope_example.json", "event_envelope.schema.json"),
    ],
)
def test_all_examples_validate(
    validator: SchemaValidator,
    examples_dir: Path,
    example_filename: str,
    schema_id: str,
) -> None:
    """Test that every example artifact validates against its contract schema."""
    with open(examples_dir / example_filename, encoding="utf-8") as f:
        instance = json.load(f)

    validator.validate(instance, schema_id)


@pytest.mark.unit
def test_problem_example_validates_by_uri_and_short_name(
    validator: SchemaValidator,
    examples_dir: Path,
) -> None:
    """Test that schema lookup works by filename, full $id URI, and short name."""
    with open(examples_dir / "problem_example.json", encoding="utf-8") as f:
        problem = json.load(f)

    validator.validate(problem, "problem.schema.json")
    validator.validate(problem, "https://pvs.ai/schemas/problem.schema.json")
    validator.validate(problem, "problem")


@pytest.mark.unit
def test_problem_missing_required_field(validator: SchemaValidator) -> None:
    """Test that missing required field fails validation."""
    invalid_problem = {
        "id": "problem-test",
        "version": 1,
    }

    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid_problem, "problem.schema.json")


@pytest.mark.unit
def test_problem_invalid_id_format(validator: SchemaValidator) -> None:
    """Test that invalid ID format fails validation."""
    invalid_problem = {
        "id": "PROBLEM-123",
        "version": 1,
        "created": "2026-11-18T10:00:00Z",
        "statement": "A valid statement that is long enough to satisfy minimum length",
        "context": {
            "user_segment": "test",
            "business_context": "test",
        },
        "constraints": {
            "synthetic_only": True,
        },
    }

    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid_problem, "problem.schema.json")


@pytest.mark.unit
def test_unknown_schema_raises_key_error(validator: SchemaValidator) -> None:
    """Test that validating against an unknown schema ID raises KeyError."""
    with pytest.raises(KeyError):
        validator.validate({"id": "test"}, "nonexistent.schema.json")


@pytest.mark.unit
def test_is_valid_returns_bool(validator: SchemaValidator) -> None:
    """Test is_valid convenience method."""
    valid = {
        "id": "problem-test",
        "version": 1,
        "created": "2026-11-18T10:00:00Z",
        "statement": "Valid problem statement with sufficient length",
        "context": {
            "user_segment": "test",
            "business_context": "test",
        },
        "constraints": {
            "synthetic_only": True,
        },
    }

    assert validator.is_valid(valid, "problem.schema.json") is True

    invalid = {"id": "bad"}
    assert validator.is_valid(invalid, "problem.schema.json") is False
