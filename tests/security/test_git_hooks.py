"""
Git hook and sanitized tracing tests (P021).
"""

import subprocess
import sys
from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from product_value_system.observability.sanitized_tracing import (
    SanitizingSpanProcessor,
    setup_sanitized_tracing,
)
from product_value_system.observability.tracing import _set_global_tracer_provider


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary Git repository."""
    repo = tmp_path / "test_repo"
    repo.mkdir()

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    scripts_dir = repo / "scripts"
    scripts_dir.mkdir()

    hook_script = (
        Path(__file__).parent.parent.parent / "scripts" / "pre-commit-secret-scan.py"
    )
    (scripts_dir / "pre-commit-secret-scan.py").write_text(
        hook_script.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    return repo


@pytest.mark.security
def test_hook_blocks_commit_with_secret(git_repo: Path) -> None:
    """Test that hook blocks commit containing secret."""
    secret_file = git_repo / "config.py"
    secret_file.write_text(
        'API_KEY = "sk-abc123def456ghi789jkl012mno345pqr678stu901vwx234"',
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "config.py"], cwd=git_repo, check=True)

    result = subprocess.run(
        [sys.executable, "scripts/pre-commit-secret-scan.py"],
        cwd=git_repo,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0, "Hook should block commit with secret"
    assert (
        b"Secrets detected" in result.stdout or b"OPENAI_API_KEY" in result.stdout
    )


@pytest.mark.security
def test_hook_allows_commit_without_secret(git_repo: Path) -> None:
    """Test that hook allows commit without secrets."""
    safe_file = git_repo / "README.md"
    safe_file.write_text("# Test Project\n\nNo secrets here.", encoding="utf-8")

    subprocess.run(["git", "add", "README.md"], cwd=git_repo, check=True)

    result = subprocess.run(
        [sys.executable, "scripts/pre-commit-secret-scan.py"],
        cwd=git_repo,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, "Hook should allow commit without secrets"


@pytest.mark.security
def test_hook_respects_secretignore_exemption(git_repo: Path) -> None:
    """Test that .secretignore exempts specified files."""
    secret_file = git_repo / "fixture_secret.py"
    secret_file.write_text(
        'API_KEY = "sk-abc123def456ghi789jkl012mno345pqr678stu901vwx234"',
        encoding="utf-8",
    )
    (git_repo / ".secretignore").write_text(
        "fixture_secret.py  # Test fixture\n",
        encoding="utf-8",
    )

    subprocess.run(
        ["git", "add", "fixture_secret.py", ".secretignore"],
        cwd=git_repo,
        check=True,
    )

    result = subprocess.run(
        [sys.executable, "scripts/pre-commit-secret-scan.py"],
        cwd=git_repo,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0


@pytest.mark.security
def test_sanitized_tracing_redacts_secrets_in_spans() -> None:
    """Test SanitizingSpanProcessor redacts secrets from span attributes."""
    setup_sanitized_tracing("test-service")

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        SanitizingSpanProcessor(SimpleSpanProcessor(exporter))
    )
    _set_global_tracer_provider(provider)

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("test.span") as span:
        span.set_attribute(
            "header",
            "Bearer sk-abc123def456ghi789jkl012mno345pqr678stu901vwx234",
        )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes is not None
    assert "sk-abc123" not in str(spans[0].attributes["header"])
    assert "[REDACTED]" in str(spans[0].attributes["header"])
