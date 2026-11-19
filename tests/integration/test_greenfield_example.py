"""
Greenfield example integration tests (P017).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from product_value_system.contracts.validation import SchemaValidator
from product_value_system.evaluation.golden_dataset import GoldenDatasetLoader


@pytest.fixture
def repo_root() -> Path:
    """Repository root path."""
    return Path(__file__).parent.parent.parent


@pytest.mark.integration
def test_greenfield_artifacts_validate(repo_root: Path) -> None:
    """Test greenfield problem and expected outcome validate against schemas."""
    validator = SchemaValidator(repo_root / "schemas")

    with open(
        repo_root / "examples" / "greenfield" / "problem_payment_flow.yaml",
        encoding="utf-8",
    ) as f:
        problem = yaml.safe_load(f)
    validator.validate(problem, "problem.schema.json")

    with open(
        repo_root / "examples" / "greenfield" / "expected_outcome.yaml",
        encoding="utf-8",
    ) as f:
        outcome = yaml.safe_load(f)
    validator.validate(outcome, "outcome.schema.json")


@pytest.mark.integration
def test_greenfield_golden_dataset_loads(repo_root: Path) -> None:
    """Test greenfield payment_flow golden dataset loads all samples."""
    loader = GoldenDatasetLoader(
        repo_root / "examples" / "greenfield" / "golden_dataset"
    )
    samples = loader.load_dataset("payment_flow")
    assert len(samples) >= 5


@pytest.mark.integration
def test_greenfield_cli_run(repo_root: Path, tmp_path: Path) -> None:
    """Test running greenfield payment flow example via CLI."""
    env = {
        **os.environ,
        "PVS_DB_PATH": str(tmp_path / "greenfield.db"),
        "PVS_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
    }
    problem_path = repo_root / "examples" / "greenfield" / "problem_payment_flow.yaml"
    res = subprocess.run(
        [sys.executable, "-m", "product_value_system.cli.main", "run", str(problem_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 0
    assert "Run completed:" in res.stdout
    run_dir = tmp_path / "artifacts" / "runs" / "problem-acme-payment-notifications"
    assert (run_dir / "problem.yaml").exists()
    assert (run_dir / "outcome.yaml").exists()
    assert (run_dir / "specification.yaml").exists()
    assert (run_dir / "architecture_review.yaml").exists()
    assert (run_dir / "security_review.yaml").exists()
