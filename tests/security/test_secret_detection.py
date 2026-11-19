"""
Secret detection and output sanitization tests (P020).
"""

import pytest

from product_value_system.security.output_sanitization import OutputSanitizer
from product_value_system.security.secret_detection import SecretDetector


@pytest.fixture
def detector() -> SecretDetector:
    """Secret detector."""
    return SecretDetector()


@pytest.mark.security
def test_detects_aws_access_key(detector: SecretDetector) -> None:
    """Test detection of AWS access key."""
    text = "aws_access_key_id=AKIAIOSFODNN7EXAMPLE"
    assert detector.contains_secrets(text) is True


@pytest.mark.security
def test_detects_openai_key(detector: SecretDetector) -> None:
    """Test detection of OpenAI API key."""
    text = "OPENAI_API_KEY=sk-abc123def456ghi789jkl012mno345pqr678stu901vwx234"
    assert detector.contains_secrets(text) is True


@pytest.mark.security
def test_detects_generic_api_key(detector: SecretDetector) -> None:
    """Test detection of generic API key."""
    text = 'api_key: "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"'
    assert detector.contains_secrets(text) is True


@pytest.mark.security
def test_detects_bearer_token(detector: SecretDetector) -> None:
    """Test detection of bearer token."""
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    assert detector.contains_secrets(text) is True


@pytest.mark.security
def test_redacts_secrets(detector: SecretDetector) -> None:
    """Test secret redaction."""
    text = "My API key is sk-abc123def456ghi789jkl012mno345pqr678stu901vwx234"
    redacted = detector.redact_secrets(text)
    assert "sk-abc123" not in redacted
    assert "[REDACTED]:OPENAI_API_KEY" in redacted


@pytest.mark.security
def test_no_false_positives(detector: SecretDetector) -> None:
    """Test no false positives on normal text."""
    text = "This is a normal problem statement about payment processing."
    assert detector.contains_secrets(text) is False


@pytest.mark.security
def test_output_sanitizer_redacts_and_truncates() -> None:
    """Test OutputSanitizer redacts secrets in dicts and truncates logs."""
    sanitizer = OutputSanitizer()
    sanitized_dict = sanitizer.sanitize_dict(
        {
            "key": "sk-abc123def456ghi789jkl012mno345pqr678stu901vwx234",
            "nested": {"aws": "AKIAIOSFODNN7EXAMPLE"},
        }
    )
    assert "[REDACTED]:OPENAI_API_KEY" in sanitized_dict["key"]
    assert "[REDACTED]:AWS_ACCESS_KEY_ID" in sanitized_dict["nested"]["aws"]

    log_str = sanitizer.sanitize_for_logging("X" * 1500, max_length=100)
    assert log_str.endswith("... [TRUNCATED]")
