"""
Brownfield example integration tests (P018).
"""

import hashlib
import json
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
def test_brownfield_problem_validates_and_drift_report_present(repo_root: Path) -> None:
    """Test brownfield problem validates against schema and drift report has 5 findings."""
    validator = SchemaValidator(repo_root / "schemas")

    with open(
        repo_root / "examples" / "brownfield" / "problem_document_legacy.yaml",
        encoding="utf-8",
    ) as f:
        problem = yaml.safe_load(f)
    validator.validate(problem, "problem.schema.json")

    with open(
        repo_root / "examples" / "brownfield" / "drift_report.json",
        encoding="utf-8",
    ) as df:
        drift = json.load(df)
    assert len(drift["drift_items"]) == 5


@pytest.mark.integration
def test_brownfield_golden_dataset_loads(repo_root: Path) -> None:
    """Test brownfield legacy_api golden dataset loads samples."""
    loader = GoldenDatasetLoader(
        repo_root / "examples" / "brownfield" / "golden_dataset"
    )
    samples = loader.load_dataset("legacy_api")
    assert len(samples) >= 2


@pytest.mark.integration
def test_brownfield_cli_run_preserves_legacy_code(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    """Test running brownfield legacy API analysis via CLI leaves legacy code unmodified."""
    java_path = (
        repo_root
        / "examples"
        / "brownfield"
        / "legacy_api"
        / "src"
        / "CustomerService.java"
    )
    before_hash = hashlib.sha256(java_path.read_bytes()).hexdigest()

    env = {
        **os.environ,
        "PVS_DB_PATH": str(tmp_path / "brownfield.db"),
        "PVS_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
    }
    problem_path = (
        repo_root / "examples" / "brownfield" / "problem_document_legacy.yaml"
    )
    res = subprocess.run(
        [sys.executable, "-m", "product_value_system.cli.main", "run", str(problem_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 0
    assert "Run completed:" in res.stdout

    after_hash = hashlib.sha256(java_path.read_bytes()).hexdigest()
    assert before_hash == after_hash
