"""
Repository for persistence operations.
"""

import json
from datetime import datetime

from product_value_system.persistence.database import Database
from product_value_system.persistence.models import (
    CostRecord,
    EvaluationRecord,
    PolicyDecisionRecord,
    RunRecord,
    StageRecord,
)


def _to_iso(dt: datetime | None) -> str | None:
    """Convert datetime to ISO 8601 string for SQLite storage."""
    return dt.isoformat() if dt is not None else None


def _from_iso(val: str | datetime | None) -> datetime | None:
    """Parse ISO 8601 string from SQLite row into datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    return datetime.fromisoformat(val)


class ValueStreamRepository:
    """
    Repository for value stream run persistence.
    """

    def __init__(self, database: Database) -> None:
        self.db = database

    def save_run(self, run: RunRecord) -> None:
        """Save value stream run."""
        outcome_val = (
            1
            if run.outcome_achieved is True
            else (0 if run.outcome_achieved is False else None)
        )
        with self.db.transaction():
            self.db.execute(
                """
                INSERT INTO value_stream_runs
                (id, problem_id, status, started_at, completed_at,
                 total_cost_usd, outcome_achieved, artifact_dir)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.problem_id,
                    run.status,
                    _to_iso(run.started_at),
                    _to_iso(run.completed_at),
                    run.total_cost_usd,
                    outcome_val,
                    run.artifact_dir,
                ),
            )

    def update_run(self, run: RunRecord) -> None:
        """Update value stream run."""
        outcome_val = (
            1
            if run.outcome_achieved is True
            else (0 if run.outcome_achieved is False else None)
        )
        with self.db.transaction():
            self.db.execute(
                """
                UPDATE value_stream_runs
                SET status = ?, completed_at = ?, total_cost_usd = ?, outcome_achieved = ?
                WHERE id = ?
                """,
                (
                    run.status,
                    _to_iso(run.completed_at),
                    run.total_cost_usd,
                    outcome_val,
                    run.id,
                ),
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        """Get value stream run by ID."""
        cursor = self.db.execute(
            "SELECT * FROM value_stream_runs WHERE id = ?",
            (run_id,),
        )

        row = cursor.fetchone()
        if not row:
            return None

        started = _from_iso(row["started_at"])
        assert started is not None
        return RunRecord(
            id=row["id"],
            problem_id=row["problem_id"],
            status=row["status"],
            started_at=started,
            completed_at=_from_iso(row["completed_at"]),
            total_cost_usd=float(row["total_cost_usd"]),
            outcome_achieved=(
                bool(row["outcome_achieved"])
                if row["outcome_achieved"] is not None
                else None
            ),
            artifact_dir=row["artifact_dir"],
        )

    def list_runs(self, limit: int = 100) -> list[RunRecord]:
        """List recent runs."""
        cursor = self.db.execute(
            """
            SELECT * FROM value_stream_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        )

        runs: list[RunRecord] = []
        for row in cursor.fetchall():
            started = _from_iso(row["started_at"])
            assert started is not None
            runs.append(
                RunRecord(
                    id=row["id"],
                    problem_id=row["problem_id"],
                    status=row["status"],
                    started_at=started,
                    completed_at=_from_iso(row["completed_at"]),
                    total_cost_usd=float(row["total_cost_usd"]),
                    outcome_achieved=(
                        bool(row["outcome_achieved"])
                        if row["outcome_achieved"] is not None
                        else None
                    ),
                    artifact_dir=row["artifact_dir"],
                )
            )

        return runs

    def save_stage(self, stage: StageRecord) -> None:
        """Save stage execution."""
        with self.db.transaction():
            self.db.execute(
                """
                INSERT INTO stages
                (id, run_id, stage_name, agent_name, status, started_at,
                 completed_at, artifact_path, cost_usd, error, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stage.id,
                    stage.run_id,
                    stage.stage_name,
                    stage.agent_name,
                    stage.status,
                    _to_iso(stage.started_at),
                    _to_iso(stage.completed_at),
                    stage.artifact_path,
                    stage.cost_usd,
                    stage.error,
                    json.dumps(stage.metadata) if stage.metadata else None,
                ),
            )

    def get_stages(self, run_id: str) -> list[StageRecord]:
        """Get all stages for a run."""
        cursor = self.db.execute(
            """
            SELECT * FROM stages
            WHERE run_id = ?
            ORDER BY started_at
            """,
            (run_id,),
        )

        stages: list[StageRecord] = []
        for row in cursor.fetchall():
            started = _from_iso(row["started_at"])
            assert started is not None
            stages.append(
                StageRecord(
                    id=row["id"],
                    run_id=row["run_id"],
                    stage_name=row["stage_name"],
                    agent_name=row["agent_name"],
                    status=row["status"],
                    started_at=started,
                    completed_at=_from_iso(row["completed_at"]),
                    artifact_path=row["artifact_path"],
                    cost_usd=float(row["cost_usd"]),
                    error=row["error"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else None,
                )
            )

        return stages

    def save_policy_decision(self, decision: PolicyDecisionRecord) -> None:
        """Save policy decision audit record."""
        with self.db.transaction():
            self.db.execute(
                """
                INSERT INTO policy_decisions
                (id, run_id, timestamp, agent_name, action, resource,
                 decision, policy_matched, rationale)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.id,
                    decision.run_id,
                    _to_iso(decision.timestamp),
                    decision.agent_name,
                    decision.action,
                    decision.resource,
                    decision.decision,
                    decision.policy_matched,
                    decision.rationale,
                ),
            )

    def get_policy_decisions(self, run_id: str) -> list[PolicyDecisionRecord]:
        """Get policy decisions for a run."""
        cursor = self.db.execute(
            """
            SELECT * FROM policy_decisions
            WHERE run_id = ?
            ORDER BY timestamp
            """,
            (run_id,),
        )
        records: list[PolicyDecisionRecord] = []
        for row in cursor.fetchall():
            ts = _from_iso(row["timestamp"])
            assert ts is not None
            records.append(
                PolicyDecisionRecord(
                    id=row["id"],
                    run_id=row["run_id"],
                    timestamp=ts,
                    agent_name=row["agent_name"],
                    action=row["action"],
                    resource=row["resource"],
                    decision=row["decision"],
                    policy_matched=row["policy_matched"],
                    rationale=row["rationale"],
                )
            )
        return records

    def save_evaluation(self, evaluation: EvaluationRecord) -> None:
        """Save evaluation result record."""
        with self.db.transaction():
            self.db.execute(
                """
                INSERT INTO evaluations
                (id, run_id, stage_id, artifact_type, judge_name, judge_type,
                 score, passed, rationale, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation.id,
                    evaluation.run_id,
                    evaluation.stage_id,
                    evaluation.artifact_type,
                    evaluation.judge_name,
                    evaluation.judge_type,
                    evaluation.score,
                    1 if evaluation.passed else 0,
                    evaluation.rationale,
                    json.dumps(evaluation.details) if evaluation.details else None,
                ),
            )

    def get_evaluations(self, run_id: str) -> list[EvaluationRecord]:
        """Get evaluation records for a run."""
        cursor = self.db.execute(
            """
            SELECT * FROM evaluations
            WHERE run_id = ?
            ORDER BY created_at
            """,
            (run_id,),
        )
        records: list[EvaluationRecord] = []
        for row in cursor.fetchall():
            records.append(
                EvaluationRecord(
                    id=row["id"],
                    run_id=row["run_id"],
                    stage_id=row["stage_id"],
                    artifact_type=row["artifact_type"],
                    judge_name=row["judge_name"],
                    judge_type=row["judge_type"],
                    score=float(row["score"]),
                    passed=bool(row["passed"]),
                    rationale=row["rationale"],
                    details=json.loads(row["details"]) if row["details"] else None,
                )
            )
        return records

    def save_cost(self, cost: CostRecord) -> None:
        """Save cost record."""
        with self.db.transaction():
            self.db.execute(
                """
                INSERT INTO costs
                (id, run_id, stage_id, timestamp, agent_name, model_name,
                 input_tokens, output_tokens, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cost.id,
                    cost.run_id,
                    cost.stage_id,
                    _to_iso(cost.timestamp),
                    cost.agent_name,
                    cost.model_name,
                    cost.input_tokens,
                    cost.output_tokens,
                    cost.cost_usd,
                ),
            )

    def get_cost_breakdown(self, run_id: str) -> dict[str, float]:
        """Get cost breakdown for a run."""
        cursor = self.db.execute(
            """
            SELECT agent_name, SUM(cost_usd) as total_cost
            FROM costs
            WHERE run_id = ?
            GROUP BY agent_name
            """,
            (run_id,),
        )

        breakdown: dict[str, float] = {}
        for row in cursor.fetchall():
            breakdown[row["agent_name"] or "unknown"] = float(row["total_cost"])

        return breakdown
