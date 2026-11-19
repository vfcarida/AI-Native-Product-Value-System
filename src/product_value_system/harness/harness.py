"""
Main harness implementation.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from opentelemetry import trace

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.adapters.model_adapter import (
    Message as AdapterMessage,
)
from product_value_system.adapters.model_adapter import (
    ModelAdapter,
    ModelResponse,
    Role,
    Usage,
)
from product_value_system.adapters.model_adapter import (
    Tool as AdapterTool,
)
from product_value_system.harness.budget import (
    Budget,
    BudgetExceededError,
    BudgetExhausted,
    EmergencyStopTriggered,
)
from product_value_system.harness.checkpoint import Checkpoint, CheckpointManager
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.guardrails import Guardrails, GuardrailViolation
from product_value_system.harness.memory import Memory
from product_value_system.harness.tools import ToolRegistry
from product_value_system.observability.tracing import traced
from product_value_system.policy.engine import PolicyEngine
from product_value_system.security.input_validator import (
    InputValidator as PromptInputValidator,
)
from product_value_system.security.input_validator import (
    SecurityError,
)

logger = logging.getLogger(__name__)


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
        input_validator: PromptInputValidator | None = None,
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
            input_validator: Prompt injection input validator (optional)
        """
        self.context = context or ExecutionContext(run_id="default-run")
        self.budget = budget or Budget()
        self.guardrails = guardrails or Guardrails()
        self.model_adapter: ModelAdapter = model_adapter or MockModelAdapter()
        self.input_validator: PromptInputValidator = (
            input_validator or PromptInputValidator()
        )

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
                role_str = msg.role.value
                content_str = msg.content
                normalized.append(msg)
            else:
                role_str = str(msg.get("role", "user"))
                content_str = str(msg.get("content", ""))
                normalized.append(AdapterMessage(role=Role(role_str), content=content_str))
                self.memory.add_message(role_str, content_str)

            if role_str == "user":
                validation = self.input_validator.validate(content_str)
                if validation.risk_level in {"MEDIUM", "HIGH", "CRITICAL"}:
                    self._log_security_event(
                        event_type="prompt_injection_detected",
                        risk_level=validation.risk_level,
                        patterns=validation.matched_patterns,
                        categories=validation.categories,
                    )
                    if not validation.is_valid:
                        self.violations.append(validation.reason)
                        raise SecurityError(
                            f"Input blocked: {validation.reason}",
                            risk_level=validation.risk_level,
                            matched_patterns=validation.matched_patterns,
                        )

        # Pre-flight emergency stop and cost budget guard (BEFORE calling LLM)
        estimated_cost, estimated_tokens = self._estimate_cost(
            normalized,
            max_tokens=max_tokens if max_tokens is not None else 2000,
        )
        try:
            self.budget.check_before_spend(
                estimated_cost_usd=estimated_cost,
                estimated_tokens=estimated_tokens,
            )
        except EmergencyStopTriggered as exc:
            logger.critical("Emergency stop triggered before model call: %s", exc)
            self.violations.append(str(exc))
            raise
        except BudgetExhausted as exc:
            logger.error("Budget exhausted before model call: %s", exc)
            self.violations.append(str(exc))
            raise

        response = await self.model_adapter.invoke(
            messages=normalized,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        if response.usage is not None:
            actual_tokens = response.usage.total_tokens
            if hasattr(self.model_adapter, "calculate_cost"):
                actual_cost = self.model_adapter.calculate_cost(
                    response.usage,
                    response.model,
                )
            else:
                actual_cost = 0.0
            self.budget.spend(Decimal(str(actual_cost)), tokens=actual_tokens)
            within_budget, error = self.budget.check_all()
            if not within_budget:
                raise BudgetExceededError(error)

        self.memory.add_message("assistant", response.content)
        return response

    def _estimate_cost(
        self,
        messages: list[AdapterMessage],
        max_tokens: int = 2000,
    ) -> tuple[float, int]:
        """Estimate pre-flight USD cost and token count before calling model."""
        est_input_tokens = max(
            1, int(sum(len(m.content.split()) * 1.3 for m in messages))
        )
        est_output_tokens = max(0, max_tokens)
        est_total_tokens = est_input_tokens + est_output_tokens

        if hasattr(self.model_adapter, "calculate_cost"):
            est_usage = Usage(
                input_tokens=est_input_tokens,
                output_tokens=est_output_tokens,
                total_tokens=est_total_tokens,
            )
            est_cost = float(self.model_adapter.calculate_cost(est_usage))
        else:
            # Conservative GPT-4 worst-case estimate ($30/1M input, $60/1M output)
            est_cost = (est_input_tokens / 1000.0) * 0.03 + (est_output_tokens / 1000.0) * 0.06

        return est_cost, est_total_tokens

    def _log_security_event(
        self,
        event_type: str,
        risk_level: str,
        patterns: list[str],
        categories: list[str],
    ) -> None:
        """Log adversarial input security event to OpenTelemetry and logger.

        Note: Only logs attack metadata (event type, risk level, categories,
        and matched rule descriptions) — never logs full raw prompt content.
        """
        span = trace.get_current_span()
        span.add_event(
            name="security.prompt_injection",
            attributes={
                "security.event_type": event_type,
                "security.risk_level": risk_level,
                "security.categories": ",".join(categories),
                "security.patterns_matched": ",".join(patterns),
                "security.pattern_count": len(patterns),
            },
        )
        logger.warning(
            "Security event: %s (risk=%s, categories=%s, matched=%d)",
            event_type,
            risk_level,
            ",".join(categories),
            len(patterns),
        )

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
