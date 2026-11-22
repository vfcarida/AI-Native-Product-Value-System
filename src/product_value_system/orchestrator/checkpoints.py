"""
Checkpoint system for recovery.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from product_value_system.harness.checkpoint import _atomic_write_json
from product_value_system.orchestrator.state_machine import (
    RunStatus,
    StageResult,
    StageStatus,
    ValueStreamRun,
)

logger = logging.getLogger(__name__)


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

    def _resolve_run_path(self, run_id: str) -> Path:
        if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id or "\x00" in run_id:
            raise ValueError(f"Unsafe checkpoint run_id: {run_id!r}")
        root = self.checkpoint_dir.resolve()
        candidate = (root / f"{run_id}.json").resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"Checkpoint path escapes directory: {candidate}")
        return candidate

    def save_checkpoint(self, run: ValueStreamRun) -> None:
        """Atomically save checkpoint for run."""
        checkpoint_file = self._resolve_run_path(run.id)

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

        _atomic_write_json(checkpoint_file, checkpoint_data)

    def load_checkpoint(self, run_id: str) -> ValueStreamRun | None:
        """Load checkpoint for run, quarantining corrupted JSON cleanly."""
        checkpoint_file = self._resolve_run_path(run_id)

        if not checkpoint_file.exists():
            return None

        try:
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
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            quarantine = checkpoint_file.with_suffix(".json.corrupt")
            logger.warning(
                "Corrupted run checkpoint %s (%s); quarantining to %s",
                checkpoint_file,
                exc,
                quarantine,
            )
            try:
                os.replace(checkpoint_file, quarantine)
            except OSError:
                pass
            return None

    def delete_checkpoint(self, run_id: str) -> None:
        """Delete checkpoint."""
        checkpoint_file = self._resolve_run_path(run_id)
        if checkpoint_file.exists():
            checkpoint_file.unlink()
