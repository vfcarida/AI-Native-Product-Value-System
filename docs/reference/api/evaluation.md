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

### Class: `GoldenDatasetLoader`, `GoldenDataset`, & `GoldenSample` (Versioned JSONL)

**Source**: [`src/product_value_system/evaluation/golden_dataset_loader.py`](../../../src/product_value_system/evaluation/golden_dataset_loader.py)

Loads, filters, subsets, validates (against [`golden_datasets/schema.json`](../../../golden_datasets/schema.json)), and SHA-256 hashes versioned JSONL golden datasets (`golden_datasets/<agent>/dataset_v<N>.jsonl`):

- `loader.load(agent_name, version=1, categories=None, tags=None, strict=False) -> GoldenDataset`
- `loader.validate_dataset(agent_name, version=1) -> list[str]`
- `loader.compute_checksum(agent_name, version=1) -> str`
- `loader.save_sample(agent_name, sample, version=1, validate=True) -> Path`
- `dataset.filter_by_category(category)`, `dataset.filter_by_tags(tags)`, `dataset.sample_subset(n, seed=42)`

---

### Class: `RegressionEvaluator`, `RegressionEvalResult`, & `RubricComplianceJudge`

**Source**: [`src/product_value_system/evaluation/regression_eval.py`](../../../src/product_value_system/evaluation/regression_eval.py)

- `RubricComplianceJudge()`: Deterministic weighted rubric judge scoring discovery, specification, build, review, brownfield, and adversarial (`block_or_sanitize`) samples.
- `RegressionEvaluator(agent, harness, eval_pipeline, baseline_path=None, harness_factory=None, max_budget_usd=10.0, regression_threshold=0.02)`: Runs golden datasets in parallel or sequentially, tracks per-sample USD/token deltas, computes pass rate / mean / P95 scores, persists baselines, and flags quality regressions.
- CLI integration: `pvs eval <agent|all> [--fast --subset-size 10] [--threshold 0.75] [--baseline-dir .eval_baselines]`.

---

### Legacy Loader (`golden_dataset.py`)

**Source**: [`src/product_value_system/evaluation/golden_dataset.py`](../../../src/product_value_system/evaluation/golden_dataset.py)

Preserved for backward compatibility (`load_dataset(name)`, `save_sample(name, sample)`).

---

### Trajectory Capture & Evaluation (`trajectory_capture.py`, `trajectory_eval.py`, `trajectory_judges.py`)

**Sources**:
- [`src/product_value_system/evaluation/trajectory_capture.py`](../../../src/product_value_system/evaluation/trajectory_capture.py)
- [`src/product_value_system/evaluation/trajectory_eval.py`](../../../src/product_value_system/evaluation/trajectory_eval.py)
- [`src/product_value_system/evaluation/trajectory_judges.py`](../../../src/product_value_system/evaluation/trajectory_judges.py)

- **`TrajectoryCapture(enabled=True, *, redact_secrets=True, sample_rate=1.0, storage_dir=None)`**: Attached to `Harness(trajectory_capture=...)` to record ReAct `TrajectoryStep` sequences (`thought`, `action`, `observation`, latency, tokens, cost, policy decisions) into an `AgentTrajectory` with automatic secret redaction.
- **`TrajectoryEvaluator(llm_judge=None, efficiency_weight=0.2, quality_weight=0.5, safety_weight=0.3, passing_threshold=0.7)`**: Evaluates step/redundancy efficiency, Levenshtein structural similarity against golden trajectories (`golden_datasets/trajectories/`), reasoning quality (LLM judge or deterministic heuristic), tool appropriateness, error recovery, and safety/policy adherence (`TrajectoryEvalResult`).
- **Composable Judges (`judge_type = "trajectory"`)**: `TrajectoryEfficiencyJudge`, `TrajectoryToolUseJudge`, `TrajectorySafetyJudge`, `TrajectoryReasoningJudge`.
- **Golden Trajectory Loaders**: `load_golden_trajectory(sample_id)` and `load_golden_trajectories(root_path, agent_name=None)`.
