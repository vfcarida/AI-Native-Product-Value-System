"""
Checkpoint management for long-running executions.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Checkpoint:
    """
    Persistent checkpoint for agent execution.
    """

    run_id: str
    stage_name: str
    agent_name: str

    iteration: int
    timestamp: datetime

    # State
    state: dict[str, Any] = field(default_factory=dict)
    memory_snapshot: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)

    # Recovery
    next_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize checkpoint."""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Checkpoint":
        """Deserialize checkpoint."""
        parsed = dict(data)
        parsed["timestamp"] = datetime.fromisoformat(parsed["timestamp"])
        return cls(**parsed)


class CheckpointManager:
    """
    Manages checkpoints to/from filesystem.
    """

    def __init__(self, checkpoint_dir: Path | str) -> None:
        """
        Initialize checkpoint manager.

        Args:
            checkpoint_dir: Directory for checkpoint files
        """
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _get_checkpoint_path(self, run_id: str, stage_name: str) -> Path:
        """Get checkpoint file path."""
        return self.checkpoint_dir / f"{run_id}_{stage_name}.json"

    def save(self, checkpoint: Checkpoint) -> Path:
        """
        Save checkpoint to disk.

        Args:
            checkpoint: Checkpoint to save

        Returns:
            Path to saved checkpoint
        """
        path = self._get_checkpoint_path(checkpoint.run_id, checkpoint.stage_name)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(checkpoint.to_dict(), f, indent=2)

        return path

    def load(self, run_id: str, stage_name: str) -> Checkpoint | None:
        """
        Load checkpoint from disk.

        Args:
            run_id: Run identifier
            stage_name: Stage name

        Returns:
            Checkpoint if exists, None otherwise
        """
        path = self._get_checkpoint_path(run_id, stage_name)

        if not path.exists():
            return None

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        return Checkpoint.from_dict(data)

    def delete(self, run_id: str, stage_name: str) -> bool:
        """
        Delete checkpoint.

        Args:
            run_id: Run identifier
            stage_name: Stage name

        Returns:
            True if deleted, False if not found
        """
        path = self._get_checkpoint_path(run_id, stage_name)

        if path.exists():
            path.unlink()
            return True

        return False

    def list_checkpoints(self, run_id: str | None = None) -> list[Checkpoint]:
        """
        List checkpoints.

        Args:
            run_id: Filter by run ID (optional)

        Returns:
            List of checkpoints
        """
        checkpoints: list[Checkpoint] = []

        for path in sorted(self.checkpoint_dir.glob("*.json")):
            if run_id and not path.stem.startswith(run_id):
                continue

            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            checkpoints.append(Checkpoint.from_dict(data))

        return checkpoints
