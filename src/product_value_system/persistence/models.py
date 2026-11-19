"""
Data models for persistence layer.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class RunRecord:
    """Value stream run record."""

    id: str
    problem_id: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    total_cost_usd: float = 0.0
    outcome_achieved: bool | None = None
    artifact_dir: str | None = None


@dataclass
class StageRecord:
    """Stage execution record."""

    id: str
    run_id: str
    stage_name: str
    agent_name: str | None
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    artifact_path: str | None = None
    cost_usd: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class PolicyDecisionRecord:
    """Policy decision record."""

    id: str
    run_id: str
    timestamp: datetime
    agent_name: str
    action: str
    resource: str
    decision: str
    policy_matched: str | None = None
    rationale: str | None = None


@dataclass
class EvaluationRecord:
    """Evaluation result record."""

    id: str
    run_id: str
    stage_id: str | None
    artifact_type: str
    judge_name: str
    judge_type: str
    score: float
    passed: bool
    rationale: str | None = None
    details: dict[str, Any] | None = None


@dataclass
class CostRecord:
    """Cost tracking record."""

    id: str
    run_id: str
    stage_id: str | None
    timestamp: datetime
    agent_name: str | None
    model_name: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float
