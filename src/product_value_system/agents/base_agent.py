"""
Base agent implementation.
"""

import time
from typing import Any

from jsonschema import ValidationError
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from product_value_system.agents.agent import AgentResult
from product_value_system.compliance.audit_logger import AuditEventType, log_audit_event_safe
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.trace_config import hash_user_id
from product_value_system.observability.tracing import traced


class BaseAgent:
    """
    Base implementation providing common agent functionality.

    Subclasses should override _execute_impl() to provide
    agent-specific logic.
    """

    def __init__(
        self,
        name: str,
        version: str,
        capabilities: list[str],
        required_tools: list[str],
        input_schema: str,
        output_schema: str,
    ) -> None:
        """
        Initialize base agent.

        Args:
            name: Agent identifier
            version: Semantic version
            capabilities: List of capabilities
            required_tools: Required tool names
            input_schema: Input JSON Schema URI
            output_schema: Output JSON Schema URI
        """
        self._name = name
        self._version = version
        self._capabilities = capabilities
        self._required_tools = required_tools
        self._input_schema = input_schema
        self._output_schema = output_schema

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    @property
    def capabilities(self) -> list[str]:
        return self._capabilities

    @property
    def required_tools(self) -> list[str]:
        return self._required_tools

    @property
    def input_schema(self) -> str:
        return self._input_schema

    @property
    def output_schema(self) -> str:
        return self._output_schema

    @traced("agent.execute")
    async def execute(
        self,
        input_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> AgentResult:
        """
        Execute agent with input validation and telemetry.

        Args:
            input_artifact: Input artifact
            harness: Harness instance
            context: Execution context

        Returns:
            AgentResult with output and telemetry
        """
        start_time = time.time()
        active_tracer = trace.get_tracer(__name__)
        root_span = trace.get_current_span()
        root_span.set_attribute("agent.name", self.name)
        root_span.set_attribute("agent.version", self.version)
        root_span.set_attribute("pvs.run_id", context.run_id)
        raw_user_id = getattr(context, "user_id", None)
        if raw_user_id:
            root_span.set_attribute("user.id_hash", hash_user_id(str(raw_user_id)))

        try:
            with active_tracer.start_as_current_span("agent.input_validation") as val_span:
                val_span.set_attribute("agent.name", self.name)
                self._validate_input(input_artifact)
        except ValidationError as e:
            root_span.set_status(Status(StatusCode.ERROR, e.message))
            return AgentResult(
                success=False,
                output_artifact={},
                iterations=0,
                cost_usd=0.0,
                duration_seconds=0.0,
                reasoning_trace=[],
                tool_calls=[],
                checkpoints=[],
                error=f"Input validation failed: {e.message}",
            )

        harness.budget.start()
        await log_audit_event_safe(
            event_type=AuditEventType.AGENT_STARTED,
            action=f"Agent {self.name} started",
            resource_type="agent",
            resource_id=self.name,
            result="started",
            user_id=context.user_id if hasattr(context, "user_id") else None,
            session_id=context.run_id,
            metadata={"agent_version": self.version},
        )
        started_trajectory = False
        if (
            harness.trajectory_capture.enabled
            and harness.trajectory_capture.get_current_trajectory() is None
        ):
            harness.trajectory_capture.start_trajectory(self.name, input_artifact)
            started_trajectory = True

        try:
            with active_tracer.start_as_current_span("agent.execution") as exec_span:
                exec_span.set_attribute("agent.name", self.name)
                result = await self._execute_impl(
                    input_artifact=input_artifact,
                    harness=harness,
                    context=context,
                )
                exec_span.set_attribute("agent.iterations", result.iterations)
                exec_span.set_attribute("agent.cost_usd", float(result.cost_usd))
                exec_span.set_attribute("agent.success", result.success)

            with active_tracer.start_as_current_span("agent.output_validation") as out_span:
                out_span.set_attribute("agent.name", self.name)
                self._validate_output(result.output_artifact)

            result.duration_seconds = time.time() - start_time
            root_span.set_attribute("agent.iterations", result.iterations)
            root_span.set_attribute("agent.cost_usd", float(result.cost_usd))
            root_span.set_attribute("agent.success", result.success)
            root_span.set_status(
                Status(StatusCode.OK)
                if result.success
                else Status(StatusCode.ERROR, result.error or "agent_failed")
            )
            if started_trajectory:
                result.trajectory = harness.trajectory_capture.finalize_trajectory(
                    result.output_artifact,
                    success=result.success,
                    error=result.error,
                )

            await log_audit_event_safe(
                event_type=(
                    AuditEventType.AGENT_COMPLETED
                    if result.success
                    else AuditEventType.AGENT_FAILED
                ),
                action=f"Agent {self.name} finished",
                resource_type="agent",
                resource_id=self.name,
                result="success" if result.success else "failure",
                user_id=context.user_id if hasattr(context, "user_id") else None,
                session_id=context.run_id,
                metadata={
                    "iterations": result.iterations,
                    "cost_usd": result.cost_usd,
                    "duration_seconds": round(result.duration_seconds, 4),
                },
            )
            return result

        except Exception as e:
            root_span.set_status(Status(StatusCode.ERROR, str(e)))
            root_span.record_exception(e)
            captured_traj = None
            if started_trajectory:
                captured_traj = harness.trajectory_capture.finalize_trajectory(
                    {},
                    success=False,
                    error=str(e),
                )
            await log_audit_event_safe(
                event_type=AuditEventType.AGENT_FAILED,
                action=f"Agent {self.name} raised unhandled error",
                resource_type="agent",
                resource_id=self.name,
                result="failure",
                user_id=context.user_id if hasattr(context, "user_id") else None,
                session_id=context.run_id,
                metadata={"error_type": type(e).__name__},
            )
            return AgentResult(
                success=False,
                output_artifact={},
                iterations=harness.budget.current_iterations,
                cost_usd=float(harness.budget.current_cost_usd),
                duration_seconds=time.time() - start_time,
                reasoning_trace=[],
                tool_calls=[],
                checkpoints=[],
                error=str(e),
                violations=harness.violations,
                trajectory=captured_traj,
            )

    async def _execute_impl(
        self,
        input_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> AgentResult:
        """
        Implementation-specific execution logic.

        Subclasses MUST override this method.
        """
        raise NotImplementedError("Subclasses must implement _execute_impl()")

    def _validate_input(self, artifact: dict[str, Any]) -> None:
        """
        Validate input artifact against schema.

        Raises:
            ValidationError: If validation fails
        """
        if not isinstance(artifact, dict):
            raise ValidationError("Input must be dictionary")

    def _validate_output(self, artifact: dict[str, Any]) -> None:
        """
        Validate output artifact against schema.

        Raises:
            ValidationError: If validation fails
        """
        if not isinstance(artifact, dict):
            raise ValidationError("Output must be dictionary")
