"""
Checkpoint system for recovery.
"""

import json
from datetime import datetime
from pathlib import Path

from product_value_system.orchestrator.state_machine import (
    RunStatus,
    StageResult,
    StageStatus,
    ValueStreamRun,
)


class CheckpointManager:
    """
    Manages checkpoints for value stream runs.
    """

    def __init__(self, checkpoint_dir: Path | str) -> None:
        """
        Initialize checkpoint manager.

        Args:
            checkpoint_dir: Directory to store checkpoints
        """
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def save_checkpoint(self, run: ValueStreamRun) -> None:
        """
        Save checkpoint for run.

        Args:
            run: Value stream run to checkpoint
        """
        checkpoint_file = self.checkpoint_dir / f"{run.id}.json"

        checkpoint_data = {
            "id": run.id,
            "problem_id": run.problem_id,
            "status": run.status.value,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "current_stage": run.current_stage,
            "total_cost_usd": run.total_cost_usd,
            "outcome_achieved": run.outcome_achieved,
            "artifact_dir": run.artifact_dir,
            "stages": {
                name: {
                    "stage_name": result.stage_name,
                    "status": result.status.value,
                    "started_at": result.started_at.isoformat(),
                    "completed_at": (
                        result.completed_at.isoformat() if result.completed_at else None
                    ),
                    "artifact_path": result.artifact_path,
                    "agent_name": result.agent_name,
                    "cost_usd": result.cost_usd,
                    "error": result.error,
                    "metadata": result.metadata,
                }
                for name, result in run.stages.items()
            },
        }

        with open(checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(checkpoint_data, f, indent=2)

    def load_checkpoint(self, run_id: str) -> ValueStreamRun | None:
        """
        Load checkpoint for run.

        Args:
            run_id: Run ID

        Returns:
            ValueStreamRun if found, None otherwise
        """
        checkpoint_file = self.checkpoint_dir / f"{run_id}.json"

        if not checkpoint_file.exists():
            return None

        with open(checkpoint_file, encoding="utf-8") as f:
            data = json.load(f)

        run = ValueStreamRun(
            id=data["id"],
            problem_id=data["problem_id"],
            status=RunStatus(data["status"]),
            started_at=(
                datetime.fromisoformat(data["started_at"]) if data["started_at"] else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data["completed_at"]
                else None
            ),
            current_stage=data["current_stage"],
            total_cost_usd=data["total_cost_usd"],
            outcome_achieved=data["outcome_achieved"],
            artifact_dir=data["artifact_dir"],
        )

        for name, stage_data in data["stages"].items():
            stage = StageResult(
                stage_name=stage_data["stage_name"],
                status=StageStatus(stage_data["status"]),
                started_at=datetime.fromisoformat(stage_data["started_at"]),
                completed_at=(
                    datetime.fromisoformat(stage_data["completed_at"])
                    if stage_data["completed_at"]
                    else None
                ),
                artifact_path=stage_data["artifact_path"],
                agent_name=stage_data["agent_name"],
                cost_usd=stage_data["cost_usd"],
                error=stage_data["error"],
                metadata=stage_data["metadata"],
            )
            run.stages[name] = stage

        return run

    def delete_checkpoint(self, run_id: str) -> None:
        """Delete checkpoint."""
        checkpoint_file = self.checkpoint_dir / f"{run_id}.json"
        if checkpoint_file.exists():
            checkpoint_file.unlink()
