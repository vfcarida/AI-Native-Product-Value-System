"""
Judge interface for evaluation framework.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class JudgeResult:
    """Result of a judge evaluation."""

    judge_name: str
    artifact_type: str
    score: float  # 0.0 to 1.0
    passed: bool
    rationale: str
    details: dict[str, Any] | None = None


class Judge(ABC):
    """
    Abstract base class for all judges.

    Judges evaluate artifacts and return scores with rationale.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Judge name (unique identifier)."""

    @property
    @abstractmethod
    def judge_type(self) -> str:
        """Judge type: deterministic, ai_based, or trajectory."""

    @abstractmethod
    async def evaluate(
        self,
        artifact: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> JudgeResult:
        """
        Evaluate an artifact.

        Args:
            artifact: Artifact to evaluate (e.g., specification, code)
            context: Optional context (e.g., original problem, golden dataset)

        Returns:
            JudgeResult with score, pass/fail, and rationale
        """
