"""
Secret detection and redaction.
"""

import re
from typing import Any


class SecretDetector:
    """
    Detects and redacts secrets in strings.
    """

    PATTERNS: list[tuple[str, str]] = [
        # Anthropic keys (specific prefix first)
        (r"sk-ant-[a-zA-Z0-9_\-]{20,}", "ANTHROPIC_API_KEY"),
        # OpenAI keys (specific prefix first)
        (r"sk-[a-zA-Z0-9]{48}", "OPENAI_API_KEY"),
        # AWS keys
        (r"AKIA[0-9A-Z]{16}", "AWS_ACCESS_KEY_ID"),
        (
            r"aws_secret_access_key\s*=\s*[A-Za-z0-9/+=]{40}",
            "AWS_SECRET_ACCESS_KEY",
        ),
        # Generic API keys
        (r"api[_-]?key\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]{32,})['\"]?", "API_KEY"),
        (r"apikey\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]{32,})['\"]?", "API_KEY"),
        # Tokens
        (r"token\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{32,})['\"]?", "TOKEN"),
        (r"bearer\s+([a-zA-Z0-9_\-\.]{20,})", "BEARER_TOKEN"),
        # Passwords
        (r"password\s*[:=]\s*['\"]([^'\"]{8,})['\"]", "PASSWORD"),
        (r"passwd\s*[:=]\s*['\"]([^'\"]{8,})['\"]", "PASSWORD"),
        # Private keys
        (r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----", "PRIVATE_KEY"),
        # Generic hex secrets (64+ characters)
        (r"\b[a-f0-9]{64,}\b", "HEX_SECRET"),
    ]

    def contains_secrets(self, text: str) -> bool:
        """
        Check if text contains any secrets.

        Returns:
            True if secrets detected
        """
        for pattern, _ in self.PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def find_secrets(self, text: str) -> list[tuple[str, str]]:
        """
        Find all secrets in text.

        Returns:
            List of (secret_value, secret_type) tuples
        """
        secrets: list[tuple[str, str]] = []

        for pattern, secret_type in self.PATTERNS:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                secret_value = match.group(0)
                secrets.append((secret_value, secret_type))

        return secrets

    def redact_secrets(self, text: str, placeholder: str = "[REDACTED]") -> str:
        """
        Redact all secrets in text.

        Args:
            text: Text to redact
            placeholder: Placeholder for redacted secrets

        Returns:
            Text with secrets redacted
        """
        redacted = text

        for pattern, secret_type in self.PATTERNS:
            redacted = re.sub(
                pattern,
                f"{placeholder}:{secret_type}",
                redacted,
                flags=re.IGNORECASE,
            )

        return redacted


def redact_dict(data: dict[str, Any], redactor: SecretDetector) -> dict[str, Any]:
    """
    Recursively redact secrets in a dictionary.

    Args:
        data: Dictionary to redact
        redactor: SecretDetector instance

    Returns:
        Dictionary with secrets redacted
    """
    redacted: dict[str, Any] = {}

    for key, value in data.items():
        if isinstance(value, str):
            redacted[key] = redactor.redact_secrets(value)
        elif isinstance(value, dict):
            redacted[key] = redact_dict(value, redactor)
        elif isinstance(value, list):
            redacted[key] = [
                redact_dict(item, redactor)
                if isinstance(item, dict)
                else redactor.redact_secrets(item)
                if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            redacted[key] = value

    return redacted
