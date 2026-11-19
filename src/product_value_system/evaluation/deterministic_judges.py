"""
Deterministic judges (schema, syntax, tests).
"""

from typing import Any

from product_value_system.contracts.validation import SchemaValidator
from product_value_system.evaluation.judge import Judge, JudgeResult


class SchemaValidationJudge(Judge):
    """Validates artifacts against JSON schemas."""

    def __init__(self, schema_validator: SchemaValidator) -> None:
        self.schema_validator = schema_validator

    @property
    def name(self) -> str:
        return "schema-validation"

    @property
    def judge_type(self) -> str:
        return "deterministic"

    async def evaluate(
        self,
        artifact: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> JudgeResult:
        """Validate artifact against its schema."""
        artifact_type = str(context.get("artifact_type", "unknown")) if context else "unknown"
        schema_name = f"{artifact_type}.schema.json"

        candidate = dict(artifact)
        if artifact_type != "code" and "code" in candidate:
            candidate.pop("code", None)

        try:
            self.schema_validator.validate(candidate, schema_name)
            return JudgeResult(
                judge_name=self.name,
                artifact_type=artifact_type,
                score=1.0,
                passed=True,
                rationale=f"Artifact conforms to {schema_name}",
            )
        except Exception as e:
            return JudgeResult(
                judge_name=self.name,
                artifact_type=artifact_type,
                score=0.0,
                passed=False,
                rationale=f"Schema validation failed: {e}",
            )


class PythonSyntaxJudge(Judge):
    """Validates Python code syntax."""

    @property
    def name(self) -> str:
        return "python-syntax"

    @property
    def judge_type(self) -> str:
        return "deterministic"

    async def evaluate(
        self,
        artifact: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> JudgeResult:
        """Check if Python code is syntactically valid."""
        code = str(artifact.get("code", ""))

        try:
            compile(code, "<string>", "exec")
            return JudgeResult(
                judge_name=self.name,
                artifact_type="code",
                score=1.0,
                passed=True,
                rationale="Python syntax is valid",
            )
        except SyntaxError as e:
            return JudgeResult(
                judge_name=self.name,
                artifact_type="code",
                score=0.0,
                passed=False,
                rationale=f"Syntax error at line {e.lineno}: {e.msg}",
            )


class TestCoverageJudge(Judge):
    """Evaluates test coverage."""

    __test__ = False

    def __init__(self, min_coverage: float = 0.8) -> None:
        self.min_coverage = min_coverage

    @property
    def name(self) -> str:
        return "test-coverage"

    @property
    def judge_type(self) -> str:
        return "deterministic"

    async def evaluate(
        self,
        artifact: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> JudgeResult:
        """Check test coverage percentage."""
        coverage = float(artifact.get("coverage_percent", 0.0)) / 100.0
        passed = coverage >= self.min_coverage

        return JudgeResult(
            judge_name=self.name,
            artifact_type="test_result",
            score=coverage,
            passed=passed,
            rationale=(
                f"Test coverage: {coverage * 100:.1f}% "
                f"(threshold: {self.min_coverage * 100:.1f}%)"
            ),
            details={"coverage_percent": coverage * 100},
        )
