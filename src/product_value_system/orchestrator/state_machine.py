"""
State machine for value stream runs.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


class RunStatus(Enum):
    """Value stream run status."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(Enum):
    """Stage status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StageResult:
    """Result of a stage execution."""

    stage_name: str
    status: StageStatus
    started_at: datetime
    completed_at: datetime | None = None
    artifact_path: str | None = None
    agent_name: str | None = None
    cost_usd: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValueStreamRun:
    """A single value stream run."""

    id: str = field(default_factory=lambda: str(uuid4()))
    problem_id: str = ""
    status: RunStatus = RunStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Stages
    stages: dict[str, StageResult] = field(default_factory=dict)
    current_stage: str | None = None

    # Costs
    total_cost_usd: float = 0.0

    # Outcome
    outcome_achieved: bool | None = None

    # Artifacts
    artifact_dir: str | None = None

    def add_stage_result(self, result: StageResult) -> None:
        """Add stage result."""
        self.stages[result.stage_name] = result
        self.total_cost_usd += result.cost_usd

    def get_stage(self, name: str) -> StageResult | None:
        """Get stage result by name."""
        return self.stages.get(name)


class ValueStreamStateMachine:
    """
    State machine for value stream progression.
    """

    STAGE_ORDER = [
        "problem_analysis",
        "outcome_hypothesis",
        "discovery",
        "decision_gate",
        "specification",
        "review_architecture",
        "review_security",
        "build",
        "quality_gate",
        "release_decision",
    ]

    def __init__(self, run: ValueStreamRun) -> None:
        self.run = run

    def get_next_stage(self) -> str | None:
        """Get next stage to execute."""
        if not self.run.current_stage:
            return self.STAGE_ORDER[0]

        try:
            current_idx = self.STAGE_ORDER.index(self.run.current_stage)
            if current_idx + 1 < len(self.STAGE_ORDER):
                return self.STAGE_ORDER[current_idx + 1]
        except ValueError:
            pass

        return None

    def can_advance(self, stage_name: str) -> bool:
        """Check if we can advance from this stage."""
        stage = self.run.get_stage(stage_name)
        if not stage:
            return False

        return stage.status == StageStatus.COMPLETED

    def is_complete(self) -> bool:
        """Check if run is complete."""
        last_stage = self.run.get_stage(self.STAGE_ORDER[-1])
        return (
            self.run.current_stage == self.STAGE_ORDER[-1]
            and last_stage is not None
            and last_stage.status == StageStatus.COMPLETED
        )
