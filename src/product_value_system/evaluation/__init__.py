"""
Evaluation framework: judges, golden dataset loader, and evaluation pipeline.
"""

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
from product_value_system.evaluation.judge import Judge, JudgeResult
from product_value_system.evaluation.pipeline import (
    EvaluationPipeline,
    EvaluationResult,
)

__all__ = [
    "ClarityJudge",
    "CompletenessJudge",
    "EvaluationPipeline",
    "EvaluationResult",
    "GoldenDatasetLoader",
    "GoldenSample",
    "Judge",
    "JudgeResult",
    "PythonSyntaxJudge",
    "SchemaValidationJudge",
    "TestCoverageJudge",
]
