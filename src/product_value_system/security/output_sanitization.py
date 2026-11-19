"""
Output sanitization utilities.
"""

from typing import Any

from product_value_system.security.secret_detection import SecretDetector, redact_dict


class OutputSanitizer:
    """
    Sanitizes outputs before logging or storage.
    """

    def __init__(self) -> None:
        self.secret_detector = SecretDetector()

    def sanitize_for_logging(self, data: Any, max_length: int = 1000) -> str:
        """
        Sanitize data for logging.

        Args:
            data: Data to sanitize
            max_length: Maximum length of output

        Returns:
            Sanitized string safe for logging
        """
        text = str(data)
        text = self.secret_detector.redact_secrets(text)

        if len(text) > max_length:
            text = text[:max_length] + "... [TRUNCATED]"

        return text

    def sanitize_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Sanitize a dictionary.

        Args:
            data: Dictionary to sanitize

        Returns:
            Dictionary with secrets redacted
        """
        return redact_dict(data, self.secret_detector)

    def sanitize_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        """
        Sanitize an artifact before storage.

        Args:
            artifact: Artifact to sanitize

        Returns:
            Sanitized artifact
        """
        sanitized = self.sanitize_dict(artifact)

        artifact_str = str(sanitized)
        if len(artifact_str) > 1_000_000:
            raise ValueError(f"Artifact too large: {len(artifact_str)} bytes")

        return sanitized
