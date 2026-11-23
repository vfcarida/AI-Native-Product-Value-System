"""
Main harness implementation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from product_value_system.evaluation.trajectory_capture import TrajectoryCapture
    from product_value_system.routing.fallback_handler import AdapterFactory
    from product_value_system.routing.model_router import ModelRouter, RoutingDecision
    from product_value_system.routing.quality_validator import QualityValidator

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
        trajectory_capture: TrajectoryCapture | None = None,
        model_router: ModelRouter | None = None,
        adapter_factory: AdapterFactory | None = None,
        quality_validator: QualityValidator | None = None,
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
            trajectory_capture: Optional trajectory capture collector (disabled by default)
        """
        self.context = context or ExecutionContext(run_id="default-run")
        self.budget = budget or Budget()
        self.guardrails = guardrails or Guardrails()
        self.model_adapter: ModelAdapter = model_adapter or MockModelAdapter()
        self.model_router: ModelRouter | None = model_router
        self.adapter_factory: AdapterFactory | None = adapter_factory
        self.quality_validator: QualityValidator | None = quality_validator
        self.last_routing_decision: RoutingDecision | None = None
        self.input_validator: PromptInputValidator = (
            input_validator or PromptInputValidator()
        )
        if trajectory_capture is not None:
            self.trajectory_capture: TrajectoryCapture = trajectory_capture
        else:
            from product_value_system.evaluation.trajectory_capture import TrajectoryCapture

            self.trajectory_capture = TrajectoryCapture(enabled=False)

        resolved_policy_engine = policy_engine or PolicyEngine()
        self.tools = ToolRegistry(resolved_policy_engine)
        self.memory = Memory()

        if checkpoint_dir:
            self.checkpoint_mgr: CheckpointManager | None = CheckpointManager(checkpoint_dir)
        else:
            self.checkpoint_mgr = None

        self.violations: list[str] = []
        self.killed = False

    def set_trajectory_capture(self, capture: TrajectoryCapture) -> None:
        """Attach or replace the active ``TrajectoryCapture`` collector."""
        self.trajectory_capture = capture

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
        capture_active = self.trajectory_capture.enabled
        step_start = time.perf_counter()

        from product_value_system.monitoring.metrics import get_metrics

        metrics = get_metrics()

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
                        metrics.record_policy_violation(
                            agent=self.context.agent_name,
                            decision="DENY",
                        )
                        metrics.record_request(
                            agent=self.context.agent_name,
                            status="error",
                            latency_seconds=time.perf_counter() - step_start,
                            error_type="SecurityError",
                        )
                        if capture_active:
                            self.trajectory_capture.log_step(
                                thought="Input validation blocked suspicious user prompt",
                                action=None,
                                observation=f"BLOCKED: {validation.reason}",
                                latency_ms=(time.perf_counter() - step_start) * 1000.0,
                                policy_checked=True,
                                policy_decision="DENY",
                            )
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
            metrics.record_request(
                agent=self.context.agent_name,
                status="error",
                latency_seconds=time.perf_counter() - step_start,
                error_type="EmergencyStopTriggered",
            )
            raise
        except BudgetExhausted as exc:
            logger.error("Budget exhausted before model call: %s", exc)
            self.violations.append(str(exc))
            metrics.record_request(
                agent=self.context.agent_name,
                status="error",
                latency_seconds=time.perf_counter() - step_start,
                error_type="BudgetExhausted",
            )
            raise

        from product_value_system.observability.genai_tracer import GenAITracer

        task_type = kwargs.pop("task_type", None)
        user_override_model = kwargs.pop("user_override_model", None)
        msg_dicts = [{"role": m.role.value, "content": m.content} for m in normalized]

        active_adapter: ModelAdapter = self.model_adapter
        routing_decision: RoutingDecision | None = None
        use_fallback = False

        if self.model_router is not None and self.model_router.enabled:
            routing_decision = self.model_router.route(
                messages=msg_dicts,
                task_type=task_type,
                metadata={
                    "agent_name": self.context.agent_name,
                    "stage_name": self.context.stage_name,
                },
                user_override_model=user_override_model,
            )
            self.last_routing_decision = routing_decision
            if not routing_decision.is_shadow and self.adapter_factory is not None:
                if (
                    self.quality_validator is not None
                    and self.model_router.policy.quality_check_enabled
                ):
                    use_fallback = True
                else:
                    active_adapter = self.adapter_factory(
                        routing_decision.provider,
                        routing_decision.model,
                    )

        adapter_model = (
            routing_decision.model
            if (routing_decision is not None and not routing_decision.is_shadow)
            else str(getattr(active_adapter, "model", "unknown"))
        )
        genai_tracer = GenAITracer(trace.get_tracer(__name__), sanitize_content=True)

        with genai_tracer.trace_chat_completion(
            operation_name=f"harness.invoke_model.{self.context.agent_name}",
            model=adapter_model,
            messages=msg_dicts,
            temperature=temperature,
            max_tokens=max_tokens,
            tool_call_count=len(tools) if tools else None,
        ) as genai_span:
            try:
                if (
                    use_fallback
                    and self.model_router is not None
                    and routing_decision is not None
                    and self.adapter_factory is not None
                ):
                    from product_value_system.routing.fallback_handler import FallbackHandler

                    handler = FallbackHandler(self.model_router, self.quality_validator)
                    response = await handler.execute_with_fallback(
                        messages=normalized,
                        routing_decision=routing_decision,
                        adapter_factory=self.adapter_factory,
                        tools=tools,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                else:
                    response = await active_adapter.invoke(
                        messages=normalized,
                        tools=tools,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                    if (
                        self.model_router is not None
                        and routing_decision is not None
                        and not routing_decision.is_shadow
                        and response.usage is not None
                    ):
                        self.model_router.record_execution(
                            model_id=response.model or routing_decision.model,
                            input_tokens=response.usage.input_tokens,
                            output_tokens=response.usage.output_tokens,
                            used_fallback=False,
                        )
            except Exception as exc:
                metrics.record_request(
                    agent=self.context.agent_name,
                    status="error",
                    latency_seconds=time.perf_counter() - step_start,
                    error_type=type(exc).__name__,
                )
                raise

            actual_cost = 0.0
            actual_tokens = 0
            prompt_tokens = 0
            completion_tokens = 0
            if response.usage is not None:
                actual_tokens = response.usage.total_tokens
                prompt_tokens = response.usage.prompt_tokens
                completion_tokens = response.usage.completion_tokens
                if hasattr(active_adapter, "calculate_cost"):
                    actual_cost = float(
                        active_adapter.calculate_cost(
                            response.usage,
                            response.model,
                        )
                    )
                elif self.model_router is not None:
                    found_spec = self.model_router.policy.find_model(
                        response.model or adapter_model
                    )
                    if found_spec is not None:
                        actual_cost = found_spec[1].estimate_cost(prompt_tokens, completion_tokens)
                self.budget.spend(Decimal(str(actual_cost)), tokens=actual_tokens)
                within_budget, error = self.budget.check_all()
                if not within_budget:
                    raise BudgetExceededError(error)

            if response.tool_calls:
                import json as _json

                for tc in response.tool_calls:
                    genai_span.record_tool_call(tc.name, _json.dumps(tc.arguments))

            genai_span.record_completion(
                response_id=str(getattr(response, "id", None) or f"resp-{self.context.run_id}"),
                completion=response.content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                finish_reason=response.finish_reason or "stop",
                response_model=response.model or adapter_model,
                cost_usd=actual_cost,
                tool_call_count=len(response.tool_calls) if response.tool_calls else None,
            )

        elapsed_sec = time.perf_counter() - step_start
        metrics.record_request(
            agent=self.context.agent_name,
            status="success",
            latency_seconds=elapsed_sec,
            cost_usd=actual_cost,
            tokens=actual_tokens,
            model=response.model or "unknown",
        )

        from product_value_system.compliance.audit_logger import (
            AuditEventType,
            log_audit_event_safe,
        )

        await log_audit_event_safe(
            event_type=AuditEventType.MODEL_CALLED,
            action=f"Model {response.model or adapter_model} invoked by {self.context.agent_name}",
            resource_type="model",
            resource_id=response.model or adapter_model,
            result="success",
            user_id=getattr(self.context, "user_id", None),
            session_id=self.context.run_id,
            metadata={
                "agent": self.context.agent_name,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": actual_tokens,
                "cost_usd": round(actual_cost, 6),
                "latency_ms": round(elapsed_sec * 1000.0, 2),
            },
        )

        self.memory.add_message("assistant", response.content)

        if capture_active:
            latency_ms = elapsed_sec * 1000.0
            thought = self._extract_thought(normalized, response.content)
            action: dict[str, Any] | None = None
            if response.tool_calls:
                first_tc = response.tool_calls[0]
                action = {
                    "tool": first_tc.name,
                    "parameters": first_tc.arguments,
                }
            self.trajectory_capture.log_step(
                thought=thought,
                action=action,
                observation=None,
                latency_ms=latency_ms,
                tokens_used=actual_tokens,
                cost_usd=actual_cost,
                policy_checked=True,
                policy_decision="ALLOW",
            )

        return response

    async def execute_tool(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a registered tool with policy check and trajectory observation logging."""
        capture_active = self.trajectory_capture.enabled
        start_time = time.perf_counter() if capture_active else 0.0
        tool_call = await self.tools.execute(
            agent_name=self.context.agent_name,
            tool_name=tool_name,
            parameters=parameters,
            context=context or {},
        )
        if capture_active:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            obs = (
                f"ERROR: {tool_call.error}"
                if tool_call.error is not None
                else str(tool_call.result)[:500]
            )
            self.trajectory_capture.attach_observation(
                observation=obs,
                tool_name=tool_name,
                parameters=parameters,
                policy_checked=True,
                policy_decision="ALLOW" if tool_call.authorized else "DENY",
                latency_ms=latency_ms,
            )
        return tool_call

    @staticmethod
    def _extract_thought(
        messages: list[AdapterMessage],
        response_content: str = "",
    ) -> str:
        """Extract ReAct ``Thought:`` reasoning from model output or latest prompt."""
        for candidate in (response_content, messages[-1].content if messages else ""):
            if "Thought:" in candidate:
                after = candidate.split("Thought:", 1)[1]
                for delimiter in ("Action:", "Final Answer:", "\n\n"):
                    if delimiter in after:
                        after = after.split(delimiter, 1)[0]
                cleaned = after.strip()
                if cleaned:
                    return cleaned[:300]
        if messages:
            return messages[-1].content.strip()[:200]
        return ""

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
