"""
Cedar entity definitions for policy evaluation.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any


class TrustLevel(str, Enum):
    """Agent trust level."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Classification(str, Enum):
    """Tool or environment classification."""

    READ = "read"
    WRITE = "write"
    EXTERNAL = "external"


class RiskLevel(str, Enum):
    """Risk level for tools."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class AgentRole:
    """Agent role entity."""

    name: str
    trust_level: TrustLevel

    def to_cedar_entity(self) -> dict[str, Any]:
        """Convert to Cedar entity format."""
        return {
            "type": "ProductValueSystem::AgentRole",
            "id": self.name,
            "attrs": {
                "name": self.name,
                "trustLevel": self.trust_level.value,
            },
        }


@dataclass
class Agent:
    """Agent entity."""

    id: str
    role: AgentRole
    version: str
    cost_budget: Decimal
    allowed_environments: set[str]

    def to_cedar_entity(self) -> dict[str, Any]:
        """Convert to Cedar entity format."""
        return {
            "type": "ProductValueSystem::Agent",
            "id": self.id,
            "attrs": {
                "role": {"type": "ProductValueSystem::AgentRole", "id": self.role.name},
                "trustLevel": self.role.trust_level.value,
                "version": self.version,
                "costBudget": float(self.cost_budget),
                "allowedEnvironments": sorted(self.allowed_environments),
            },
        }


@dataclass
class Tool:
    """Tool entity."""

    name: str
    classification: Classification
    risk_level: RiskLevel
    data_access: set[str]
    requires_approval: bool

    def to_cedar_entity(self) -> dict[str, Any]:
        """Convert to Cedar entity format."""
        return {
            "type": "ProductValueSystem::Tool",
            "id": self.name,
            "attrs": {
                "classification": self.classification.value,
                "riskLevel": self.risk_level.value,
                "dataAccess": sorted(self.data_access),
                "requiresApproval": self.requires_approval,
            },
        }


@dataclass
class AuthorizationContext:
    """Context for policy evaluation."""

    environment: str
    has_human_approval: bool
    cost_so_far: Decimal
    iteration_count: int
    stage: str

    def to_cedar_context(self) -> dict[str, Any]:
        """Convert to Cedar context format."""
        return {
            "environment": self.environment,
            "has_human_approval": self.has_human_approval,
            "cost_so_far": float(self.cost_so_far),
            "iteration_count": self.iteration_count,
            "stage": self.stage,
        }
