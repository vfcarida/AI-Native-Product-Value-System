# How-To: Write Evaluations (Judges)

**Goal**: Create custom judges to evaluate agent outputs.

**Time**: 30-45 minutes

**Prerequisites**:
- Understanding of [Evaluation Framework](../reference/api/evaluation.md)
- Familiarity with [Judge Interface](../reference/api/evaluation.md#judge)

---

## Step 1: Define Evaluation Criteria

Clearly define what you're evaluating. Example:

> **Criteria**: Specification must include error handling for all external API calls.

---

## Step 2: Choose Judge Type

- **Deterministic Judge** (`judge_type = "deterministic"`): Fast, rule-based (regex, JSON schema, test execution, coverage).
- **AI-Based Judge** (`judge_type = "ai_based"`): LLM evaluates semantic quality, completeness, or clarity against rubrics.

For this example: Deterministic judge checking for error handling coverage.

---

## Step 3: Create Judge Class

Create: `src/product_value_system/evaluation/error_handling_judge.py`

```python
"""
Judge that checks for error handling in specifications.
"""

from typing import Any

from product_value_system.evaluation.judge import Judge, JudgeResult


class ErrorHandlingJudge(Judge):
    """
    Evaluates whether specification includes error handling.
    """

    @property
    def name(self) -> str:
        return "error-handling"

    @property
    def judge_type(self) -> str:
        return "deterministic"

    async def evaluate(
        self,
        artifact: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> JudgeResult:
        """
        Check if specification mentions error handling.

        Args:
            artifact: Specification artifact
            context: Optional evaluation context

        Returns:
            JudgeResult with score and rationale
        """
        spec_content = str(artifact.get("content", artifact))

        error_keywords = [
            "error handling",
            "exception",
            "try-catch",
            "error response",
            "fallback",
            "retry",
            "timeout",
        ]

        matches = [
            keyword
            for keyword in error_keywords
            if keyword.lower() in spec_content.lower()
        ]

        coverage = len(matches) / len(error_keywords)
        passed = coverage >= 0.3  # At least 30% keyword coverage

        if passed:
            rationale = f"Error handling mentioned: {', '.join(matches)}"
        else:
            rationale = "No error handling found in specification"

        return JudgeResult(
            judge_name=self.name,
            artifact_type="specification",
            score=coverage,
            passed=passed,
            rationale=rationale,
            details={"matches": matches},
        )
```

---

## Step 4: Write Tests

Create: `tests/unit/test_error_handling_judge.py`

```python
"""
Tests for error handling judge.
"""

import pytest

from product_value_system.evaluation.judge import Judge, JudgeResult


@pytest.mark.asyncio
async def test_error_handling_judge_passes_with_keywords() -> None:
    """Test judge passes when error handling keywords are present."""
    judge = ErrorHandlingJudge()

    artifact = {
        "content": """
        ## Error Handling

        The API will return error responses with appropriate HTTP status codes.
        All external API calls will have timeout and retry logic.
        """
    }

    result = await judge.evaluate(artifact)

    assert result.passed is True
    assert result.score > 0.3
    assert "error handling" in result.rationale.lower()


@pytest.mark.asyncio
async def test_error_handling_judge_fails_without_keywords() -> None:
    """Test judge fails when no error handling is mentioned."""
    judge = ErrorHandlingJudge()

    artifact = {
        "content": "API for payment processing with credit cards."
    }

    result = await judge.evaluate(artifact)

    assert result.passed is False
    assert result.score < 0.3
```

Run:

```bash
pytest tests/unit/test_error_handling_judge.py -v
```

---

## Step 5: Add to Evaluation Pipeline

Pass your custom judge into `EvaluationPipeline` (`src/product_value_system/evaluation/pipeline.py`):

```python
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.evaluation.ai_judges import ClarityJudge, CompletenessJudge
from product_value_system.evaluation.deterministic_judges import SchemaValidationJudge
from product_value_system.evaluation.pipeline import EvaluationPipeline


def create_specification_pipeline(model_adapter) -> EvaluationPipeline:
    """Create evaluation pipeline for specifications."""
    judges = [
        SchemaValidationJudge(SchemaValidator()),
        CompletenessJudge(model_adapter),
        ClarityJudge(model_adapter),
        ErrorHandlingJudge(),  # Custom judge
    ]

    return EvaluationPipeline(judges=judges, passing_threshold=0.7)
```

---

## Step 6: Test Pipeline Integration

```python
"""
Integration test for error handling judge in pipeline.
"""

import pytest

from product_value_system.evaluation.pipeline import EvaluationPipeline


@pytest.mark.asyncio
async def test_pipeline_includes_error_handling_judge() -> None:
    """Test pipeline executes custom judge."""
    pipeline = EvaluationPipeline(judges=[ErrorHandlingJudge()], passing_threshold=0.3)

    artifact = {
        "content": "Specification with error handling, retry, and timeout policies."
    }

    result = await pipeline.evaluate(artifact, context={"artifact_type": "specification"})

    judge_names = [r.judge_name for r in result.judge_results]
    assert "error-handling" in judge_names
    assert result.passed is True
```

---

## AI-Based Judge Example

For semantic quality evaluation using `ModelAdapter`:

```python
import json
from typing import Any

from product_value_system.adapters.model_adapter import Message, ModelAdapter, Role
from product_value_system.evaluation.judge import Judge, JudgeResult


class CodeQualityJudge(Judge):
    """AI-based judge for code quality."""

    def __init__(self, model_adapter: ModelAdapter) -> None:
        self.model_adapter = model_adapter

    @property
    def name(self) -> str:
        return "code-quality"

    @property
    def judge_type(self) -> str:
        return "ai_based"

    async def evaluate(
        self,
        artifact: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> JudgeResult:
        """Evaluate code quality using LLM."""
        code = artifact.get("code", "")

        prompt = f"""Evaluate this code for quality:

{code}

Rate 0.0 to 1.0 on:
- Readability
- Maintainability
- Error handling
- Performance considerations

Respond in JSON:
{{
  "score": 0.85,
  "rationale": "Brief explanation"
}}"""

        response = await self.model_adapter.invoke(
            messages=[
                Message(role=Role.SYSTEM, content="You are a code quality expert."),
                Message(role=Role.USER, content=prompt),
            ],
            temperature=0.0,
        )

        parsed = json.loads(response.content)
        score = float(parsed["score"])

        return JudgeResult(
            judge_name=self.name,
            artifact_type="code",
            score=score,
            passed=score >= 0.7,
            rationale=str(parsed["rationale"]),
        )
```

---

## Troubleshooting

### Judge not running in pipeline

**Problem**: Custom judge is not executed during `pipeline.evaluate()`.

**Solution**: Ensure an instance of the judge is included in the `judges` list passed to `EvaluationPipeline(judges=...)` (Step 5).

---

### AI judge returns invalid JSON

**Error**: `JSONDecodeError: Expecting value`

**Solution**: Strip Markdown code fences (` ```json ... ``` `) before calling `json.loads()`, or see `_parse_json_response()` in `src/product_value_system/evaluation/ai_judges.py`.

---

## Next Steps

- [Create Custom Agent](create-custom-agent.md) that uses evaluations
- See [Evaluation API Reference](../reference/api/evaluation.md) for details

---

**Last Updated**: 2026-11-18
