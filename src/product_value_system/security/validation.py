"""
Input validation utilities.
"""

import re
from typing import Any

from product_value_system.contracts.validation import SchemaValidator
from product_value_system.security.secret_detection import SecretDetector


class ValidationError(Exception):
    """Input validation error."""


class InputValidator:
    """
    Validates user inputs against security rules.
    """

    def __init__(self, schema_validator: SchemaValidator) -> None:
        self.schema_validator = schema_validator

    def validate_problem_statement(self, problem: dict[str, Any]) -> None:
        """
        Validate problem statement.

        Raises:
            ValidationError: If validation fails
        """
        statement = str(problem.get("statement", ""))
        if len(statement) > 10000:
            raise ValidationError("Problem statement too long (max 10KB)")

        if self._detect_prompt_injection(statement):
            raise ValidationError("Potential prompt injection detected")

        try:
            self.schema_validator.validate(problem, "problem.schema.json")
        except Exception as e:
            raise ValidationError(f"Schema validation failed: {e}") from e

    def validate_agent_output(
        self,
        output: dict[str, Any],
        expected_schema: str,
    ) -> None:
        """
        Validate agent output.

        Raises:
            ValidationError: If validation fails
        """
        if self._detect_output_anomalies(output):
            raise ValidationError("Output anomalies detected")

        try:
            self.schema_validator.validate(output, expected_schema)
        except Exception as e:
            raise ValidationError(f"Output schema validation failed: {e}") from e

    def _detect_prompt_injection(self, text: str) -> bool:
        """
        Detect potential prompt injection patterns.

        Returns:
            True if injection detected
        """
        patterns = [
            r"ignore\s+previous\s+instructions",
            r"disregard\s+.*\s+above",
            r"forget\s+.*\s+instructions",
            r"new\s+instructions",
            r"system\s*:\s*",
            r"<\|im_start\|>",
            r"<\|im_end\|>",
        ]

        text_lower = text.lower()

        for pattern in patterns:
            if re.search(pattern, text_lower):
                return True

        return False

    def _detect_output_anomalies(self, output: dict[str, Any]) -> bool:
        """
        Detect anomalies in agent output.

        Returns:
            True if anomalies detected
        """
        output_str = str(output)
        if len(output_str) > 100_000:
            return True

        secret_detector = SecretDetector()
        if secret_detector.contains_secrets(output_str):
            return True

        return False
