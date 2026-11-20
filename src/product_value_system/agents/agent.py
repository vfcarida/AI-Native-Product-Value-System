"""
Agent protocol definition.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness


@dataclass
class AgentResult:
    """
    Result of agent execution.
    """

    success: bool
    output_artifact: dict[str, Any]

    # Telemetry
    iterations: int
    cost_usd: float
    duration_seconds: float

    # Evidence
    reasoning_trace: list[str]
    tool_calls: list[dict[str, Any]]
    checkpoints: list[str]

    # Errors
    error: str | None = None
    violations: list[str] = field(default_factory=list)

    # Optional captured execution trajectory (EVO-012)
    trajectory: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize result to dictionary."""
        payload: dict[str, Any] = {
            "success": self.success,
            "output_artifact": self.output_artifact,
            "iterations": self.iterations,
            "cost_usd": self.cost_usd,
            "duration_seconds": self.duration_seconds,
            "reasoning_trace": self.reasoning_trace,
            "tool_calls": self.tool_calls,
            "checkpoints": self.checkpoints,
            "error": self.error,
            "violations": self.violations,
        }
        if self.trajectory is not None:
            payload["trajectory"] = (
                self.trajectory.to_dict()
                if hasattr(self.trajectory, "to_dict")
                else self.trajectory
            )
        return payload


@runtime_checkable
class Agent(Protocol):
    """
    Protocol for all agents in the system.

    Agents are goal-oriented entities that operate within harnesses,
    producing structured outputs from structured inputs.
    """

    @property
    def name(self) -> str:
        """
        Unique agent identifier.

        Example: "discovery-agent", "specification-agent"
        """
        ...

    @property
    def version(self) -> str:
        """
        Agent version (semantic versioning).

        Example: "0.1.0"
        """
        ...

    @property
    def capabilities(self) -> list[str]:
        """
        List of capabilities this agent provides.

        Example: ["problem_analysis", "outcome_generation"]
        """
        ...

    @property
    def required_tools(self) -> list[str]:
        """
        List of tools this agent requires.

        Example: ["knowledge.query", "synthesis.generate"]
        """
        ...

    @property
    def input_schema(self) -> str:
        """
        JSON Schema URI for expected input artifact.

        Example: "https://pvs.ai/schemas/problem.schema.json"
        """
        ...

    @property
    def output_schema(self) -> str:
        """
        JSON Schema URI for produced output artifact.

        Example: "https://pvs.ai/schemas/outcome.schema.json"
        """
        ...

    async def execute(
        self,
        input_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> AgentResult:
        """
        Execute agent task.

        Args:
            input_artifact: Structured input (validated against input_schema)
            harness: Harness providing tools, memory, budgets
            context: Execution context with run metadata

        Returns:
            AgentResult with output artifact and telemetry

        Raises:
            ValidationError: If input_artifact invalid
            BudgetExceededError: If budget exhausted
            GuardrailViolation: If constraints violated
        """
        ...
