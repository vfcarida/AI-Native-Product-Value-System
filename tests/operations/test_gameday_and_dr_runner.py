"""
Tests for Executable PROD-015 Continuous Improvement & Operational Cycle (v3.0):
- ``GameDayScenarioRunner`` (GAMEDAY-001..004 + blameless report + Kaizen backlog)
- ``DRDrillVerifier`` (online backup -> total loss -> restore -> SHA-256 + HMAC parity)
- ``DORAAIBaselineCollector`` (aggregate-only, anonymized DORA AI 7-Capabilities + SPACE)
- Zero-warning CLI invocations: ``pvs ops gameday``, ``pvs ops dr-drill``,
  ``pvs ops dora-baseline`` under ``python -W error``
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import product_value_system
from product_value_system.operations import (
    DORAAIBaselineCollector,
    DRDrillVerifier,
    GameDayScenarioRunner,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_gameday_runner_dr_drill_and_dora_baseline_first_cycle(tmp_path: Path) -> None:
    """Execute the complete first PROD-015 operational cycle deterministically at $0."""
    # 1. Chaos GameDay suite (GAMEDAY-001..004)
    runner = GameDayScenarioRunner(environment="staging-dryrun")
    gd_report = await runner.run_suite()
    assert gd_report.overall_passed is True
    assert gd_report.passed_scenarios == 4
    assert gd_report.total_scenarios == 4
    assert gd_report.platform_version == product_value_system.__version__
    assert len(gd_report.kaizen_backlog) == 4

    gd_md = gd_report.render_markdown()
    assert "# Chaos Engineering GameDay Report" in gd_md
    assert "GAMEDAY-001" in gd_md and "GAMEDAY-004" in gd_md

    with pytest.raises(ValueError, match="Unknown GameDay scenario"):
        await runner.run_scenario("GAMEDAY-999")

    # 2. Quarterly Disaster Recovery backup/restore + SHA-256 + HMAC verification drill
    dr_report = await DRDrillVerifier().execute_drill(work_dir=tmp_path / "dr")
    assert dr_report.passed is True
    assert dr_report.backup_sha256 == dr_report.restored_sha256
    assert len(dr_report.backup_sha256) == 64
    assert dr_report.runs_verified == 3
    assert dr_report.audit_events_verified == 3
    assert dr_report.audit_hmac_valid is True
    assert dr_report.rto_seconds < dr_report.rto_target_seconds
    assert dr_report.rpo_seconds < dr_report.rpo_target_seconds
    assert "# Quarterly Disaster Recovery Drill Report" in dr_report.render_markdown()

    # 3. Privacy-preserving DORA AI 7-Capabilities & SPACE baseline snapshot
    dora_report = await DORAAIBaselineCollector().collect(
        config_path=REPO_ROOT / "config" / "metrics.yaml"
    )
    assert dora_report.aggregate_only is True
    assert dora_report.anonymize is True
    assert "performance_tier" in dora_report.dora_metrics
    assert "# DORA AI 7-Capabilities & SPACE Baseline Snapshot" in dora_report.render_markdown()


def test_cli_pvs_ops_subcommands_run_zero_warnings(tmp_path: Path) -> None:
    """``pvs ops gameday|dr-drill|dora-baseline`` execute cleanly under ``python -W error``."""
    gd_proc = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-m",
            "product_value_system",
            "ops",
            "gameday",
            "--scenario",
            "GAMEDAY-001",
            "--format",
            "json",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert gd_proc.returncode == 0, gd_proc.stderr
    gd_json = json.loads(gd_proc.stdout)
    assert gd_json["overall_passed"] is True
    assert gd_json["passed_scenarios"] == 1

    dr_md_path = tmp_path / "dr-drill.md"
    dr_proc = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-m",
            "product_value_system",
            "ops",
            "dr-drill",
            "--format",
            "markdown",
            "--output",
            str(dr_md_path),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert dr_proc.returncode == 0, dr_proc.stderr
    assert dr_md_path.is_file()
    assert "Result**: `PASS`" in dr_md_path.read_text(encoding="utf-8")

    dora_proc = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-m",
            "product_value_system",
            "ops",
            "dora-baseline",
            "--format",
            "json",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert dora_proc.returncode == 0, dora_proc.stderr
    dora_json = json.loads(dora_proc.stdout)
    assert dora_json["aggregate_only"] is True
    assert dora_json["anonymize"] is True
