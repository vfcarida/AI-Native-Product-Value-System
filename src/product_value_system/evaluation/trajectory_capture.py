"""
Agent trajectory capture, serialization, secret redaction, and golden trajectory loading (EVO-012).

Captures ReAct thought-action-observation sequences during agent execution inside
``Harness`` with minimal overhead (<5% when enabled; near-zero no-op when disabled)
and automatic secret redaction via ``SecretDetector``.
"""

import json
import logging
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from product_value_system.security.secret_detection import SecretDetector

logger = logging.getLogger(__name__)


@dataclass
class TrajectoryStep:
    """Single thought-action-observation step in an agent execution trajectory."""

    step_index: int
    timestamp: datetime

    # ReAct components
    thought: str
    action: dict[str, Any] | None = None
    observation: str | None = None

    # Performance & cost metadata
    latency_ms: float = 0.0
    tokens_used: int = 0
    cost_usd: float = 0.0

    # Policy / safety metadata
    policy_checked: bool = False
    policy_decision: str | None = None  # "ALLOW" | "DENY" | None

    def __repr__(self) -> str:
        action_label = self.action.get("tool") if self.action else "final_answer"
        preview = self.thought[:50]
        return f"Step {self.step_index}: {preview}... -> {action_label}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize step to JSON-compatible dictionary."""
        return {
            "step_index": self.step_index,
            "timestamp": self.timestamp.isoformat(),
            "thought": self.thought,
            "action": self.action,
            "observation": self.observation,
            "latency_ms": round(self.latency_ms, 3),
            "tokens_used": self.tokens_used,
            "cost_usd": round(self.cost_usd, 6),
            "policy_checked": self.policy_checked,
            "policy_decision": self.policy_decision,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrajectoryStep":
        """Reconstruct a ``TrajectoryStep`` from serialized JSON dict."""
        raw_ts = data.get("timestamp")
        ts = datetime.fromisoformat(raw_ts) if isinstance(raw_ts, str) else datetime.now(UTC)
        return cls(
            step_index=int(data.get("step_index", 1)),
            timestamp=ts,
            thought=str(data.get("thought", "")),
            action=dict(data["action"]) if isinstance(data.get("action"), dict) else None,
            observation=(
                str(data["observation"]) if data.get("observation") is not None else None
            ),
            latency_ms=float(data.get("latency_ms", 0.0)),
            tokens_used=int(data.get("tokens_used", 0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
            policy_checked=bool(data.get("policy_checked", False)),
            policy_decision=(
                str(data["policy_decision"]) if data.get("policy_decision") is not None else None
            ),
        )


@dataclass
class AgentTrajectory:
    """Complete execution trajectory captured for a single agent invocation."""

    trajectory_id: str
    agent_name: str
    input_artifact: dict[str, Any]
    output_artifact: dict[str, Any]

    steps: list[TrajectoryStep] = field(default_factory=list)

    # Aggregate metrics
    total_steps: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0

    # Execution outcome
    success: bool = True
    error: str | None = None
    sample_id: str | None = None

    # Timestamps
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def add_step(self, step: TrajectoryStep) -> None:
        """Append a step and update aggregate counters."""
        self.steps.append(step)
        self.total_steps += 1
        self.total_tokens += step.tokens_used
        self.total_cost_usd += step.cost_usd
        self.total_latency_ms += step.latency_ms

    def finalize(self) -> None:
        """Mark trajectory execution as finished."""
        self.completed_at = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """Serialize trajectory to JSON-compatible dictionary."""
        payload: dict[str, Any] = {
            "trajectory_id": self.trajectory_id,
            "agent_name": self.agent_name,
            "input_artifact": self.input_artifact,
            "output_artifact": self.output_artifact,
            "steps": [step.to_dict() for step in self.steps],
            "total_steps": self.total_steps,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_latency_ms": round(self.total_latency_ms, 3),
            "success": self.success,
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
        if self.sample_id is not None:
            payload["sample_id"] = self.sample_id
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentTrajectory":
        """Reconstruct ``AgentTrajectory`` from a JSON/JSONL dictionary."""
        raw_started = data.get("started_at")
        started_at = (
            datetime.fromisoformat(raw_started)
            if isinstance(raw_started, str)
            else datetime.now(UTC)
        )
        raw_completed = data.get("completed_at")
        completed_at = (
            datetime.fromisoformat(raw_completed) if isinstance(raw_completed, str) else None
        )

        trajectory_id = str(data.get("trajectory_id") or data.get("id") or str(uuid4()))
        trajectory = cls(
            trajectory_id=trajectory_id,
            agent_name=str(data.get("agent_name", "unknown")),
            input_artifact=dict(data.get("input_artifact", {})),
            output_artifact=dict(data.get("output_artifact", {})),
            success=bool(data.get("success", True)),
            error=str(data["error"]) if data.get("error") is not None else None,
            sample_id=str(data["sample_id"]) if data.get("sample_id") is not None else None,
            started_at=started_at,
            completed_at=completed_at,
        )
        for raw_step in data.get("steps", []):
            if isinstance(raw_step, dict):
                trajectory.add_step(TrajectoryStep.from_dict(raw_step))
        return trajectory

    def save(self, output_path: Path | str) -> Path:
        """Persist trajectory to JSON (or single-line JSONL when suffix is ``.jsonl``)."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        if path.suffix == ".jsonl":
            path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path | str) -> "AgentTrajectory":
        """Load an ``AgentTrajectory`` from a JSON or single-record JSONL file."""
        file_path = Path(path)
        raw_text = file_path.read_text(encoding="utf-8").strip()
        if file_path.suffix == ".jsonl":
            first_line = next(line for line in raw_text.splitlines() if line.strip())
            data = json.loads(first_line)
        else:
            data = json.loads(raw_text)
        return cls.from_dict(data)


class TrajectoryCapture:
    """
    Capture agent execution trajectories inside ``Harness``.

    Supports:
    - Near-zero overhead when ``enabled=False``
    - Probabilistic sampling via ``sample_rate`` (0.0 to 1.0)
    - Automatic secret redaction via ``SecretDetector`` before persistence
    - Optional automatic file persistence via ``storage_dir``
    """

    def __init__(
        self,
        enabled: bool = True,
        *,
        redact_secrets: bool = True,
        sample_rate: float = 1.0,
        storage_dir: Path | str | None = None,
        seed: int | None = None,
    ) -> None:
        self.enabled = enabled
        self.redact_secrets = redact_secrets
        self.sample_rate = max(0.0, min(1.0, sample_rate))
        self.storage_dir = Path(storage_dir) if storage_dir is not None else None
        self._rng = random.Random(seed)
        self._secret_detector = SecretDetector() if redact_secrets else None

        self.current_trajectory: AgentTrajectory | None = None
        self.last_trajectory: AgentTrajectory | None = None
        self.completed_trajectories: list[AgentTrajectory] = []
        self.step_counter = 0
        self._active_sampled = False

    def _sanitize_text(self, text: str | None) -> str | None:
        if text is None or self._secret_detector is None:
            return text
        return self._secret_detector.redact_secrets(text)

    def _sanitize_mapping(self, obj: Any) -> Any:
        if self._secret_detector is None:
            return obj
        if isinstance(obj, str):
            return self._secret_detector.redact_secrets(obj)
        if isinstance(obj, dict):
            return {k: self._sanitize_mapping(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._sanitize_mapping(v) for v in obj]
        return obj

    def start_trajectory(
        self,
        agent_name: str,
        input_artifact: dict[str, Any],
        *,
        sample_id: str | None = None,
    ) -> None:
        """Start capturing a new agent execution trajectory."""
        if not self.enabled:
            return
        if self.sample_rate < 1.0 and self._rng.random() > self.sample_rate:
            self._active_sampled = False
            self.current_trajectory = None
            return

        self._active_sampled = True
        self.current_trajectory = AgentTrajectory(
            trajectory_id=str(uuid4()),
            agent_name=agent_name,
            input_artifact=self._sanitize_mapping(input_artifact),
            output_artifact={},
            sample_id=sample_id,
        )
        self.step_counter = 0

    def log_step(
        self,
        thought: str,
        action: dict[str, Any] | None = None,
        observation: str | None = None,
        latency_ms: float = 0.0,
        tokens_used: int = 0,
        cost_usd: float = 0.0,
        policy_checked: bool = False,
        policy_decision: str | None = None,
    ) -> None:
        """Log a single step to the active trajectory."""
        if not self.enabled or not self._active_sampled or self.current_trajectory is None:
            return

        self.step_counter += 1
        sanitized_thought = self._sanitize_text(thought) or ""
        sanitized_action = self._sanitize_mapping(action) if action is not None else None
        sanitized_obs = self._sanitize_text(observation)

        step = TrajectoryStep(
            step_index=self.step_counter,
            timestamp=datetime.now(UTC),
            thought=sanitized_thought,
            action=sanitized_action,
            observation=sanitized_obs,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            policy_checked=policy_checked,
            policy_decision=policy_decision,
        )
        self.current_trajectory.add_step(step)

    def attach_observation(
        self,
        observation: str,
        *,
        tool_name: str | None = None,
        parameters: dict[str, Any] | None = None,
        policy_checked: bool | None = None,
        policy_decision: str | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        """Attach a tool observation to the latest step or append a dedicated tool step."""
        if not self.enabled or not self._active_sampled or self.current_trajectory is None:
            return

        sanitized_obs = self._sanitize_text(observation)
        if (
            self.current_trajectory.steps
            and self.current_trajectory.steps[-1].observation is None
            and self.current_trajectory.steps[-1].action is not None
        ):
            latest = self.current_trajectory.steps[-1]
            latest.observation = sanitized_obs
            latest.latency_ms += latency_ms
            self.current_trajectory.total_latency_ms += latency_ms
            if policy_checked is not None:
                latest.policy_checked = policy_checked
            if policy_decision is not None:
                latest.policy_decision = policy_decision
            return

        self.log_step(
            thought=f"Invoking tool '{tool_name or 'unknown'}'",
            action={"tool": tool_name or "unknown", "parameters": parameters or {}},
            observation=sanitized_obs,
            latency_ms=latency_ms,
            policy_checked=bool(policy_checked),
            policy_decision=policy_decision,
        )

    def finalize_trajectory(
        self,
        output_artifact: dict[str, Any],
        success: bool = True,
        error: str | None = None,
    ) -> AgentTrajectory | None:
        """Complete the current trajectory, optionally persist it, and reset active state."""
        if not self.enabled or not self._active_sampled or self.current_trajectory is None:
            return None

        self.current_trajectory.output_artifact = self._sanitize_mapping(output_artifact)
        self.current_trajectory.success = success
        self.current_trajectory.error = self._sanitize_text(error)
        self.current_trajectory.finalize()

        trajectory = self.current_trajectory
        self.last_trajectory = trajectory
        self.completed_trajectories.append(trajectory)
        self.current_trajectory = None
        self._active_sampled = False

        if self.storage_dir is not None:
            trajectory.save(self.storage_dir / f"{trajectory.trajectory_id}.json")

        return trajectory

    def get_current_trajectory(self) -> AgentTrajectory | None:
        """Return the currently in-progress trajectory (if any)."""
        return self.current_trajectory

    def get_trajectory(self) -> AgentTrajectory | None:
        """Return the active trajectory, or the most recently finalized trajectory."""
        return self.current_trajectory or self.last_trajectory


def load_golden_trajectory(
    sample_id: str,
    root_path: Path | str = Path("golden_datasets/trajectories"),
) -> AgentTrajectory | None:
    """
    Load a golden reference trajectory matching ``sample_id`` (e.g., ``discovery-001``).

    Looks for ``<sample_id>-golden.jsonl`` or ``<sample_id>.jsonl`` under ``root_path``.
    """
    root = Path(root_path)
    if not root.exists():
        return None

    for candidate_name in (
        f"{sample_id}-golden.jsonl",
        f"{sample_id}.jsonl",
        f"{sample_id}-golden.json",
    ):
        candidate = root / candidate_name
        if candidate.exists():
            return AgentTrajectory.load(candidate)
    return None


def load_golden_trajectories(
    root_path: Path | str = Path("golden_datasets/trajectories"),
    agent_name: str | None = None,
) -> list[AgentTrajectory]:
    """Load all golden trajectories under ``root_path``, optionally filtered by agent name."""
    root = Path(root_path)
    if not root.exists():
        return []

    trajectories: list[AgentTrajectory] = []
    for file_path in sorted(root.glob("*.jsonl")):
        traj = AgentTrajectory.load(file_path)
        if agent_name is not None and traj.agent_name != agent_name:
            continue
        trajectories.append(traj)
    return trajectories
