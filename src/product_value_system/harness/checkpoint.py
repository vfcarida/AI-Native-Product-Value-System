"""
Checkpoint management for long-running executions.
"""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically persist JSON using same-directory temp file + fsync + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp_cp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


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
        """Get validated checkpoint file path contained inside ``checkpoint_dir``."""
        for label, value in (("run_id", run_id), ("stage_name", stage_name)):
            if not value or "/" in value or "\\" in value or ".." in value or "\x00" in value:
                raise ValueError(f"Unsafe checkpoint {label}: {value!r}")
        root = self.checkpoint_dir.resolve()
        candidate = (root / f"{run_id}_{stage_name}.json").resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"Checkpoint path escapes directory: {candidate}")
        return candidate

    def save(self, checkpoint: Checkpoint) -> Path:
        """Atomically save checkpoint to disk."""
        path = self._get_checkpoint_path(checkpoint.run_id, checkpoint.stage_name)
        _atomic_write_json(path, checkpoint.to_dict())
        return path

    def load(self, run_id: str, stage_name: str) -> Checkpoint | None:
        """Load checkpoint from disk, quarantining corrupted JSON files cleanly."""
        path = self._get_checkpoint_path(run_id, stage_name)

        if not path.exists():
            return None

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return Checkpoint.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            quarantine = path.with_suffix(".json.corrupt")
            logger.warning(
                "Corrupted checkpoint %s (%s); quarantining to %s",
                path,
                exc,
                quarantine,
            )
            try:
                os.replace(path, quarantine)
            except OSError:
                pass
            return None

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
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                checkpoints.append(Checkpoint.from_dict(data))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipping corrupted checkpoint %s: %s", path, exc)

        return checkpoints
