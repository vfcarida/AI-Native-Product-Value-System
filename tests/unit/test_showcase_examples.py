"""Automated verification that all zero-cost showcase demos execute cleanly (Round 18)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("script_rel", "expected_marker"),
    [
        ("examples/showcases/finops_optimization_demo.py", "CACHE_HIT"),
        ("examples/showcases/brownfield_modernization_demo.py", "Structural Modularity Q"),
        ("examples/showcases/parallel_devx_demo.py", "SPACE Overall"),
    ],
)
def test_zero_cost_showcase_script_runs_cleanly(
    script_rel: str,
    expected_marker: str,
) -> None:
    script = REPO_ROOT / script_rel
    assert script.is_file(), f"Missing showcase script: {script}"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, (
        f"{script_rel} failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert expected_marker in proc.stdout
