"""
CLI integration tests (P016).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


def _run_pvs(
    args: list[str],
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke pvs CLI via python -m product_value_system.cli.main."""
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "product_value_system.cli.main", *args],
        capture_output=True,
        text=True,
        env=merged_env,
        check=False,
    )


@pytest.fixture
def cli_env(tmp_path: Path) -> dict[str, str]:
    """CLI environment."""
    return {
        "PVS_DB_PATH": str(tmp_path / "test.db"),
        "PVS_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
    }


@pytest.mark.integration
def test_cli_version() -> None:
    """Test CLI version."""
    result = _run_pvs(["--version"])

    assert result.returncode == 0
    assert "pvs" in result.stdout


@pytest.mark.integration
def test_cli_help() -> None:
    """Test CLI help."""
    result = _run_pvs(["--help"])

    assert result.returncode == 0
    assert "init" in result.stdout
    assert "run" in result.stdout
    assert "status" in result.stdout
    assert "list" in result.stdout
    assert "show" in result.stdout
    assert "validate" in result.stdout


@pytest.mark.integration
def test_cli_init(tmp_path: Path, cli_env: dict[str, str]) -> None:
    """Test CLI init command."""
    output_path = tmp_path / "problem.yaml"

    result = _run_pvs(["init", "--output", str(output_path)], env=cli_env)

    assert result.returncode == 0
    assert output_path.exists()


@pytest.mark.integration
def test_cli_validate(tmp_path: Path) -> None:
    """Test CLI validate command."""
    problem_path = tmp_path / "problem.yaml"

    problem = {
        "id": "problem-test-001",
        "version": 1,
        "created": "2026-11-18T10:00:00Z",
        "statement": "Test problem statement with sufficient length for validation.",
        "context": {"user_segment": "test", "business_context": "test"},
        "constraints": {"synthetic_only": True},
    }

    with open(problem_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(problem, f)

    result = _run_pvs(["validate", str(problem_path), "--type", "problem"])

    assert result.returncode == 0
    assert "valid" in result.stdout.lower()


@pytest.mark.integration
def test_cli_list_empty(cli_env: dict[str, str]) -> None:
    """Test CLI list with no runs."""
    result = _run_pvs(["list"], env=cli_env)

    assert result.returncode == 0
    assert "No runs found" in result.stdout


@pytest.mark.integration
def test_cli_end_to_end_run_list_status_show(
    tmp_path: Path,
    cli_env: dict[str, str],
) -> None:
    """Test full CLI workflow: init -> run -> list -> status -> show."""
    problem_path = tmp_path / "demo-problem.yaml"

    init_res = _run_pvs(["init", "--output", str(problem_path)], env=cli_env)
    assert init_res.returncode == 0

    run_res = _run_pvs(["run", str(problem_path)], env=cli_env)
    assert run_res.returncode == 0
    assert "Run completed:" in run_res.stdout

    # Extract run_id from output
    run_id = ""
    for line in run_res.stdout.splitlines():
        if "Run completed:" in line:
            run_id = line.split("Run completed:")[-1].strip()
            break
    assert run_id != ""

    list_res = _run_pvs(["list"], env=cli_env)
    assert list_res.returncode == 0
    assert run_id[:12] in list_res.stdout

    status_res = _run_pvs(["status", run_id], env=cli_env)
    assert status_res.returncode == 0
    assert "completed" in status_res.stdout.lower()

    show_res = _run_pvs(["show", run_id], env=cli_env)
    assert show_res.returncode == 0
    assert run_id in show_res.stdout
