"""
Tool registry and execution wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from opentelemetry import trace

from product_value_system.observability.tracing import traced
from product_value_system.policy.engine import AuthorizationDecision, PolicyEngine
from product_value_system.security.agentic_guardrails import ToolOutputSanitizer
from product_value_system.security.input_validator import InputValidator


@runtime_checkable
class Tool(Protocol):
    """Protocol for tool implementations."""

    @property
    def name(self) -> str:
        """Tool identifier."""
        ...

    @property
    def description(self) -> str:
        """Tool description for LLM."""
        ...

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for tool parameters."""
        ...

    async def execute(self, **kwargs: Any) -> Any:
        """Execute tool with parameters."""
        ...


@dataclass
class ToolCall:
    """Record of a tool invocation."""

    tool_name: str
    parameters: dict[str, Any]
    result: Any
    authorized: bool
    error: str | None = None


class ToolRegistry:
    """
    Registry of available tools with policy-gated execution and OWASP ASI01
    indirect prompt injection validation on tool outputs.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        *,
        output_validator: InputValidator | None = None,
        validate_outputs: bool = True,
    ) -> None:
        """
        Initialize tool registry.

        Args:
            policy_engine: Policy engine for authorization
            output_validator: Optional InputValidator for scanning tool results (OWASP ASI01)
            validate_outputs: Enable indirect prompt injection scanning on tool outputs
        """
        self.policy_engine = policy_engine
        self.tools: dict[str, Tool] = {}
        self._output_validator: InputValidator | None = (
            (output_validator or InputValidator()) if validate_outputs else None
        )
        self._output_sanitizer: ToolOutputSanitizer | None = (
            ToolOutputSanitizer(input_validator=self._output_validator)
            if validate_outputs
            else None
        )

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self.tools[tool.name] = tool

    def unregister(self, tool_name: str) -> None:
        """Unregister a tool."""
        self.tools.pop(tool_name, None)

    def get_tool(self, name: str) -> Tool | None:
        """Get tool by name."""
        return self.tools.get(name)

    def list_tools(self) -> list[str]:
        """List all registered tool names."""
        return list(self.tools.keys())

    @traced("tool.authorize")
    def authorize_call(
        self,
        agent_name: str,
        tool_name: str,
        parameters: dict[str, Any],
        context: dict[str, Any],
    ) -> AuthorizationDecision:
        """
        Authorize tool call via policy engine.

        Args:
            agent_name: Name of agent requesting tool
            tool_name: Tool to call
            parameters: Tool parameters
            context: Execution context

        Returns:
            Authorization decision
        """
        return self.policy_engine.authorize(
            principal={"type": "Agent", "name": agent_name},
            action={"type": "call"},
            resource={"type": "Tool", "name": tool_name},
            context={"parameters": parameters, **context},
        )

    @traced("tool.execute")
    async def execute(
        self,
        agent_name: str,
        tool_name: str,
        parameters: dict[str, Any],
        context: dict[str, Any],
    ) -> ToolCall:
        """
        Execute tool with policy authorization.

        Args:
            agent_name: Name of agent requesting tool
            tool_name: Tool to execute
            parameters: Tool parameters
            context: Execution context

        Returns:
            ToolCall record with result or error

        Raises:
            ValueError: If tool not found
        """
        span = trace.get_current_span()
        span.set_attribute("tool.name", tool_name)
        span.set_attribute("agent.name", agent_name)

        tool = self.get_tool(tool_name)
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not registered")

        decision = self.authorize_call(agent_name, tool_name, parameters, context)
        span.set_attribute("authorization.decision", decision.decision.value)

        if not decision.is_allowed():
            error_msg = f"Tool call denied: {decision.reason}"
            span.set_attribute("error.message", error_msg)
            return ToolCall(
                tool_name=tool_name,
                parameters=parameters,
                result=None,
                authorized=False,
                error=error_msg,
            )

        try:
            result = await tool.execute(**parameters)
            if self._output_sanitizer is not None and result is not None:
                serialized = result if isinstance(result, str) else str(result)
                sanitized = self._output_sanitizer.sanitize(serialized)
                if not sanitized.allowed:
                    reason = "; ".join(sanitized.findings)
                    error_msg = (
                        f"Tool output blocked (OWASP ASI01/ASI04 indirect injection / "
                        f"tool poisoning): {reason}"
                    )
                    span.set_attribute("tool.status", "blocked_indirect_injection")
                    span.set_attribute("error.message", error_msg)
                    return ToolCall(
                        tool_name=tool_name,
                        parameters=parameters,
                        result=None,
                        authorized=True,
                        error=error_msg,
                    )
                if isinstance(result, str) and sanitized.findings:
                    result = sanitized.sanitized_text
            span.set_attribute("tool.status", "success")

            return ToolCall(
                tool_name=tool_name,
                parameters=parameters,
                result=result,
                authorized=True,
            )

        except Exception as e:
            error_msg = f"Tool execution failed: {e}"
            span.set_attribute("tool.status", "error")
            span.record_exception(e)

            return ToolCall(
                tool_name=tool_name,
                parameters=parameters,
                result=None,
                authorized=True,
                error=error_msg,
            )
