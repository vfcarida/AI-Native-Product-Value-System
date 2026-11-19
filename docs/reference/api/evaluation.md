# API Reference: Evaluation Framework

## Module: `product_value_system.evaluation`

**Source**: [`src/product_value_system/evaluation/`](../../../src/product_value_system/evaluation/)

---

### Abstract Class: `Judge` & `JudgeResult`

**Source**: [`src/product_value_system/evaluation/judge.py`](../../../src/product_value_system/evaluation/judge.py)

```python
class Judge(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def judge_type(self) -> str: ...  # "deterministic", "ai_based", "trajectory"

    @abstractmethod
    async def evaluate(
        self,
        artifact: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> JudgeResult: ...
```

---

### Deterministic Judges (`deterministic_judges.py`)

**Source**: [`src/product_value_system/evaluation/deterministic_judges.py`](../../../src/product_value_system/evaluation/deterministic_judges.py)

- `SchemaValidationJudge(schema_validator)` — Validates artifact against `<artifact_type>.schema.json`.
- `PythonSyntaxJudge()` — Verifies generated Python code compiles without `SyntaxError`.
- `TestCoverageJudge(min_coverage=0.8)` — Enforces minimum test coverage percentage threshold.

---

### AI-Based Judges (`ai_judges.py`)

**Source**: [`src/product_value_system/evaluation/ai_judges.py`](../../../src/product_value_system/evaluation/ai_judges.py)

- `CompletenessJudge(model_adapter)` — Scores artifact completeness (0.0–1.0, passing threshold 0.7).
- `ClarityJudge(model_adapter)` — Scores artifact clarity and unambiguity (0.0–1.0, passing threshold 0.7).

---

### Class: `EvaluationPipeline` & `EvaluationResult`

**Source**: [`src/product_value_system/evaluation/pipeline.py`](../../../src/product_value_system/evaluation/pipeline.py)

Executes multiple judges in parallel (`asyncio.gather`), enforces `passing_threshold` and mandatory deterministic judge success, and converts results to schema-valid evidence via `EvaluationResult.to_evidence_artifact()`.

---

### Class: `GoldenDatasetLoader` & `GoldenSample`

**Source**: [`src/product_value_system/evaluation/golden_dataset.py`](../../../src/product_value_system/evaluation/golden_dataset.py)

Loads and persists regression golden datasets (`load_dataset(name)`, `save_sample(name, sample)`).
