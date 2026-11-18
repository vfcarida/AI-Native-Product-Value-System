"""
Main harness implementation.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_value_system.harness.budget import Budget, BudgetExceededError
from product_value_system.harness.checkpoint import Checkpoint, CheckpointManager
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.guardrails import Guardrails, GuardrailViolation
from product_value_system.harness.memory import Memory
from product_value_system.harness.tools import ToolRegistry
from product_value_system.observability.tracing import traced
from product_value_system.policy.engine import PolicyEngine


@dataclass
class HarnessResult:
    """Result of harness execution."""

    success: bool
    output: Any
    iterations: int
    cost_usd: float
    checkpoints: list[str]
    violations: list[str]
    error: str | None = None


class Harness:
    """
    Harness wrapping agent execution with controls.
    """

    def __init__(
        self,
        context: ExecutionContext,
        policy_engine: PolicyEngine,
        budget: Budget,
        guardrails: Guardrails,
        checkpoint_dir: Path | None = None,
    ) -> None:
        """
        Initialize harness.

        Args:
            context: Execution context
            policy_engine: Policy engine for authorization
            budget: Resource budget
            guardrails: Safety constraints
            checkpoint_dir: Directory for checkpoints (optional)
        """
        self.context = context
        self.budget = budget
        self.guardrails = guardrails

        self.tools = ToolRegistry(policy_engine)
        self.memory = Memory()

        if checkpoint_dir:
            self.checkpoint_mgr: CheckpointManager | None = CheckpointManager(checkpoint_dir)
        else:
            self.checkpoint_mgr = None

        self.violations: list[str] = []
        self.killed = False

    def kill(self) -> None:
        """Kill switch: stop execution immediately."""
        self.killed = True

    def is_killed(self) -> bool:
        """Check if killed."""
        return self.killed

    @traced("harness.check_constraints")
    def check_constraints(self, iteration: int) -> None:
        """
        Check all constraints (budget, guardrails, kill switch).

        Raises:
            BudgetExceededError: If budget exceeded
            GuardrailViolation: If guardrail violated
            RuntimeError: If killed
        """
        if self.killed:
            raise RuntimeError("Execution killed by kill switch")

        within_budget, error = self.budget.check_all()
        if not within_budget:
            raise BudgetExceededError(error)

        passed, error = self.guardrails.check_iteration_limit(iteration)
        if not passed:
            assert error is not None
            self.violations.append(error)
            raise GuardrailViolation(error)

    def save_checkpoint(self, iteration: int, state: dict[str, Any]) -> Path | None:
        """
        Save checkpoint.

        Args:
            iteration: Current iteration
            state: State dictionary

        Returns:
            Path to checkpoint if manager configured
        """
        if not self.checkpoint_mgr:
            return None

        checkpoint = Checkpoint(
            run_id=self.context.run_id,
            stage_name=self.context.stage_name,
            agent_name=self.context.agent_name,
            iteration=iteration,
            timestamp=datetime.now(UTC),
            state=state,
            memory_snapshot=self.memory.to_dict(),
        )

        return self.checkpoint_mgr.save(checkpoint)

    def load_checkpoint(self) -> Checkpoint | None:
        """
        Load checkpoint for resumption.

        Returns:
            Checkpoint if exists
        """
        if not self.checkpoint_mgr:
            return None

        return self.checkpoint_mgr.load(
            self.context.run_id,
            self.context.stage_name,
        )
