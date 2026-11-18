"""
PII and sensitive data sanitization for telemetry.
"""

import re
from typing import Any

# Patterns for sensitive data
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_PATTERN = re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
CREDIT_CARD_PATTERN = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")

# Keys that should never be logged
SENSITIVE_KEYS = {
    "password",
    "secret",
    "api_key",
    "token",
    "credential",
    "authorization",
    "auth",
    "private_key",
    "access_key",
}

# Metric keys representing LLM token counts (not secret tokens)
TOKEN_METRIC_KEYS = {
    "tokens",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "max_tokens",
    "prompt_tokens",
    "completion_tokens",
}


def _is_sensitive_key(key: str) -> bool:
    """Return True if attribute key represents a secret or credential."""
    lowered = key.lower()
    if lowered in TOKEN_METRIC_KEYS or lowered.endswith("_tokens"):
        return False
    return any(sensitive in lowered for sensitive in SENSITIVE_KEYS)


def sanitize_string(value: str) -> str:
    """
    Sanitize a string by redacting PII.

    Args:
        value: Input string

    Returns:
        Sanitized string with PII redacted
    """
    value = EMAIL_PATTERN.sub("[EMAIL_REDACTED]", value)
    value = CREDIT_CARD_PATTERN.sub("[CARD_REDACTED]", value)
    value = SSN_PATTERN.sub("[SSN_REDACTED]", value)
    value = PHONE_PATTERN.sub("[PHONE_REDACTED]", value)
    return value


def sanitize_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """
    Sanitize attribute dictionary for telemetry.

    Args:
        attributes: Attribute dictionary

    Returns:
        Sanitized dictionary with PII removed
    """
    sanitized: dict[str, Any] = {}

    for key, value in attributes.items():
        if _is_sensitive_key(key):
            continue

        if isinstance(value, str):
            sanitized[key] = sanitize_string(value)
        elif isinstance(value, (int, float, bool)):
            sanitized[key] = value
        else:
            continue

    return sanitized
