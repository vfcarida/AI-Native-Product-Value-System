"""
Safety guardrails for agent execution.
"""

import re
from typing import Any


class Guardrails:
    """
    Enforces safety constraints on agent behavior.
    """

    def __init__(
        self,
        max_iterations: int = 10,
        blocked_patterns: list[str] | None = None,
        required_output_schema: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize guardrails.

        Args:
            max_iterations: Maximum reasoning iterations
            blocked_patterns: Regex patterns to block in output
            required_output_schema: JSON Schema for output validation
        """
        self.max_iterations = max_iterations
        self.blocked_patterns = [re.compile(p) for p in (blocked_patterns or [])]
        self.required_output_schema = required_output_schema

    def check_iteration_limit(self, current: int) -> tuple[bool, str | None]:
        """
        Check if iteration limit exceeded.

        Returns:
            (within_limit, error_message)
        """
        if current > self.max_iterations:
            return False, f"Iteration limit exceeded: {current} > {self.max_iterations}"
        return True, None

    def check_content(self, content: str) -> tuple[bool, str | None]:
        """
        Check content against blocked patterns.

        Returns:
            (allowed, error_message)
        """
        for pattern in self.blocked_patterns:
            if pattern.search(content):
                return False, f"Content matches blocked pattern: {pattern.pattern}"
        return True, None

    def validate_output(self, output: Any) -> tuple[bool, str | None]:
        """
        Validate output against schema.

        Returns:
            (valid, error_message)
        """
        if not self.required_output_schema:
            return True, None

        try:
            from jsonschema import ValidationError, validate

            validate(instance=output, schema=self.required_output_schema)
            return True, None
        except ValidationError as e:
            return False, f"Output schema validation failed: {e.message}"

    def enforce_all(
        self,
        iteration: int,
        content: str | None = None,
        output: Any | None = None,
    ) -> tuple[bool, str | None]:
        """
        Enforce all guardrails.

        Returns:
            (passed, error_message)
        """
        passed, error = self.check_iteration_limit(iteration)
        if not passed:
            return False, error

        if content:
            passed, error = self.check_content(content)
            if not passed:
                return False, error

        if output is not None:
            passed, error = self.validate_output(output)
            if not passed:
                return False, error

        return True, None


class GuardrailViolation(Exception):  # noqa: N818
    """Raised when guardrail violated."""
