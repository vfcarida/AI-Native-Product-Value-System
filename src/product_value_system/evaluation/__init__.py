"""
Evaluation framework: judges, golden datasets, trajectory evaluation, and regression evaluation.
"""

from product_value_system.evaluation.ai_judges import ClarityJudge, CompletenessJudge
from product_value_system.evaluation.deterministic_judges import (
    PythonSyntaxJudge,
    SchemaValidationJudge,
    TestCoverageJudge,
)
from product_value_system.evaluation.golden_dataset import (
    GoldenDatasetLoader as LegacyGoldenDatasetLoader,
)
from product_value_system.evaluation.golden_dataset import (
    GoldenSample as LegacyGoldenSample,
)
from product_value_system.evaluation.golden_dataset_loader import (
    GoldenDataset,
    GoldenDatasetLoader,
    GoldenSample,
)
from product_value_system.evaluation.judge import Judge, JudgeResult
from product_value_system.evaluation.pipeline import (
    EvaluationPipeline,
    EvaluationResult,
)
from product_value_system.evaluation.regression_eval import (
    RegressionEvalResult,
    RegressionEvaluator,
    RubricComplianceJudge,
    SampleEvalResult,
)
from product_value_system.evaluation.trajectory_capture import (
    AgentTrajectory,
    TrajectoryCapture,
    TrajectoryStep,
    load_golden_trajectories,
    load_golden_trajectory,
)
from product_value_system.evaluation.trajectory_eval import (
    TrajectoryEvalResult,
    TrajectoryEvaluator,
)
from product_value_system.evaluation.trajectory_judges import (
    TrajectoryEfficiencyJudge,
    TrajectoryReasoningJudge,
    TrajectorySafetyJudge,
    TrajectoryToolUseJudge,
)

__all__ = [
    "AgentTrajectory",
    "ClarityJudge",
    "CompletenessJudge",
    "EvaluationPipeline",
    "EvaluationResult",
    "GoldenDataset",
    "GoldenDatasetLoader",
    "GoldenSample",
    "Judge",
    "JudgeResult",
    "LegacyGoldenDatasetLoader",
    "LegacyGoldenSample",
    "PythonSyntaxJudge",
    "RegressionEvalResult",
    "RegressionEvaluator",
    "RubricComplianceJudge",
    "SampleEvalResult",
    "SchemaValidationJudge",
    "TestCoverageJudge",
    "TrajectoryCapture",
    "TrajectoryEfficiencyJudge",
    "TrajectoryEvalResult",
    "TrajectoryEvaluator",
    "TrajectoryReasoningJudge",
    "TrajectorySafetyJudge",
    "TrajectoryStep",
    "TrajectoryToolUseJudge",
    "load_golden_trajectories",
    "load_golden_trajectory",
]
