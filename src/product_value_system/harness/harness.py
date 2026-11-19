"""
Main harness implementation.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.adapters.model_adapter import (
    Message as AdapterMessage,
)
from product_value_system.adapters.model_adapter import (
    ModelAdapter,
    ModelResponse,
    Role,
)
from product_value_system.adapters.model_adapter import (
    Tool as AdapterTool,
)
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
        context: ExecutionContext | None = None,
        policy_engine: PolicyEngine | None = None,
        budget: Budget | None = None,
        guardrails: Guardrails | None = None,
        checkpoint_dir: Path | None = None,
        model_adapter: ModelAdapter | None = None,
    ) -> None:
        """
        Initialize harness.

        Args:
            context: Execution context
            policy_engine: Policy engine for authorization
            budget: Resource budget
            guardrails: Safety constraints
            checkpoint_dir: Directory for checkpoints (optional)
            model_adapter: Model adapter for LLM invocations (optional)
        """
        self.context = context or ExecutionContext(run_id="default-run")
        self.budget = budget or Budget()
        self.guardrails = guardrails or Guardrails()
        self.model_adapter: ModelAdapter = model_adapter or MockModelAdapter()

        resolved_policy_engine = policy_engine or PolicyEngine()
        self.tools = ToolRegistry(resolved_policy_engine)
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

    @traced("harness.invoke_model")
    async def invoke_model(
        self,
        messages: list[AdapterMessage | dict[str, Any]],
        tools: list[AdapterTool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """
        Invoke underlying model adapter within harness constraints.
        """
        self.budget.record_iteration()
        self.check_constraints(self.budget.current_iterations)

        normalized: list[AdapterMessage] = []
        for msg in messages:
            if isinstance(msg, AdapterMessage):
                normalized.append(msg)
            else:
                role_str = str(msg.get("role", "user"))
                content_str = str(msg.get("content", ""))
                normalized.append(AdapterMessage(role=Role(role_str), content=content_str))
                self.memory.add_message(role_str, content_str)

        response = await self.model_adapter.invoke(
            messages=normalized,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        if response.usage is not None and hasattr(self.model_adapter, "calculate_cost"):
            cost_val = self.model_adapter.calculate_cost(response.usage, response.model)
            self.budget.record_cost(Decimal(str(cost_val)))
            within_budget, error = self.budget.check_all()
            if not within_budget:
                raise BudgetExceededError(error)

        self.memory.add_message("assistant", response.content)
        return response

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
