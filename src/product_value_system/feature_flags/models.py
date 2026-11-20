"""
Feature flag and progressive delivery data models (EVO-015).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class FlagType(str, Enum):
    """Martin Fowler feature flag taxonomy."""

    RELEASE = "release"  # Long-lived, controls incremental feature rollout
    EXPERIMENT = "experiment"  # Temporary, A/B testing & multivariate experiments
    OPS = "ops"  # Circuit breakers, degradation toggles, kill switches
    PERMISSION = "permission"  # Entitlement / access control per user or tier


class RolloutStage(str, Enum):
    """Progressive delivery stages from dark launch to full release."""

    DARK = "dark"  # 0% user traffic (shadow / internal verification)
    CANARY = "canary"  # 1-5% user traffic with strict SLO gates
    STAGED_5 = "staged_5"  # 5% traffic
    STAGED_25 = "staged_25"  # 25% traffic
    STAGED_50 = "staged_50"  # 50% traffic
    FULL = "full"  # 100% traffic


STAGE_ORDER: tuple[RolloutStage, ...] = (
    RolloutStage.DARK,
    RolloutStage.CANARY,
    RolloutStage.STAGED_5,
    RolloutStage.STAGED_25,
    RolloutStage.STAGED_50,
    RolloutStage.FULL,
)

STAGE_PERCENTAGES: dict[RolloutStage, float] = {
    RolloutStage.DARK: 0.0,
    RolloutStage.CANARY: 0.05,
    RolloutStage.STAGED_5: 0.05,
    RolloutStage.STAGED_25: 0.25,
    RolloutStage.STAGED_50: 0.50,
    RolloutStage.FULL: 1.0,
}

DEFAULT_STAGE_QUALITY_GATES: dict[RolloutStage, dict[str, float]] = {
    RolloutStage.DARK: {"error_rate": 0.0, "crash_rate": 0.0},
    RolloutStage.CANARY: {"error_rate": 0.01, "quality_score": 0.95},
    RolloutStage.STAGED_5: {"error_rate": 0.01, "quality_score": 0.95},
    RolloutStage.STAGED_25: {"error_rate": 0.01, "quality_score": 0.95},
    RolloutStage.STAGED_50: {"error_rate": 0.01, "quality_score": 0.95},
    RolloutStage.FULL: {"error_rate": 0.01, "quality_score": 0.95},
}


def _parse_iso_datetime(value: str | datetime | None) -> datetime:
    """Parse ISO-8601 timestamp string into timezone-aware UTC datetime."""
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@dataclass
class FeatureFlag:
    """Feature flag definition controlling progressive rollout and targeting."""

    name: str
    description: str = ""
    type: FlagType = FlagType.RELEASE
    enabled: bool = False
    rollout_percentage: float = 0.0
    rollout_stage: RolloutStage = RolloutStage.DARK
    targeting_rules: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if isinstance(self.type, str):
            self.type = FlagType(self.type)
        if isinstance(self.rollout_stage, str):
            self.rollout_stage = RolloutStage(self.rollout_stage)
        if not 0.0 <= self.rollout_percentage <= 1.0:
            raise ValueError(
                f"rollout_percentage must be between 0.0 and 1.0, got {self.rollout_percentage}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize flag to YAML/JSON dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "type": self.type.value,
            "enabled": self.enabled,
            "rollout_stage": self.rollout_stage.value,
            "rollout_percentage": round(float(self.rollout_percentage), 6),
            "targeting_rules": list(self.targeting_rules),
            "created_at": self.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "updated_at": self.updated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FeatureFlag:
        """Deserialize flag from validated dictionary."""
        return cls(
            name=str(data["name"]),
            description=str(data.get("description", "")),
            type=FlagType(data.get("type", FlagType.RELEASE.value)),
            enabled=bool(data.get("enabled", False)),
            rollout_percentage=float(data.get("rollout_percentage", 0.0)),
            rollout_stage=RolloutStage(data.get("rollout_stage", RolloutStage.DARK.value)),
            targeting_rules=list(data.get("targeting_rules", [])),
            created_at=_parse_iso_datetime(data.get("created_at")),
            updated_at=_parse_iso_datetime(data.get("updated_at")),
        )


@dataclass(frozen=True)
class FlagEvaluation:
    """Audit record returned by each feature flag evaluation."""

    flag_name: str
    enabled: bool
    reason: str
    user_id: str | None = None
    request_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Serialize evaluation outcome to dictionary."""
        return {
            "flag_name": self.flag_name,
            "enabled": self.enabled,
            "reason": self.reason,
            "user_id": self.user_id,
            "request_id": self.request_id,
            "timestamp": self.timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }


@dataclass
class RolloutPlan:
    """Progressive rollout schedule with stage durations and quality gates."""

    flag_name: str
    stages: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"stage": RolloutStage.DARK.value, "percentage": 0.0, "duration_hours": 24},
            {"stage": RolloutStage.CANARY.value, "percentage": 0.05, "duration_hours": 48},
            {"stage": RolloutStage.STAGED_5.value, "percentage": 0.05, "duration_hours": 24},
            {"stage": RolloutStage.STAGED_25.value, "percentage": 0.25, "duration_hours": 24},
            {"stage": RolloutStage.STAGED_50.value, "percentage": 0.50, "duration_hours": 24},
            {"stage": RolloutStage.FULL.value, "percentage": 1.0, "duration_hours": 0},
        ]
    )
    current_stage: RolloutStage = RolloutStage.DARK
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    quality_gates: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            stage.value: dict(gates) for stage, gates in DEFAULT_STAGE_QUALITY_GATES.items()
        }
    )
    auto_rollback_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize rollout plan to dictionary."""
        return {
            "flag_name": self.flag_name,
            "stages": list(self.stages),
            "current_stage": self.current_stage.value,
            "started_at": self.started_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "quality_gates": dict(self.quality_gates),
            "auto_rollback_enabled": self.auto_rollback_enabled,
        }


@dataclass(frozen=True)
class AuditEntry:
    """Immutable audit log entry for flag configuration mutations."""

    action: str
    flag_name: str
    actor: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Serialize audit log entry to dictionary."""
        return {
            "action": self.action,
            "flag_name": self.flag_name,
            "actor": self.actor,
            "details": dict(self.details),
            "timestamp": self.timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
