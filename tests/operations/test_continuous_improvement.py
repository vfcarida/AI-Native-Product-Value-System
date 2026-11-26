"""
Automated validation suite for PROD-015: Continuous Improvement Framework.

Validates:
- 15 Chaos Engineering GameDay scenarios + README catalog in docs/operations/gameday-scenarios/
- All 5 improvement guides (gameday facilitation, retrospective, DR drills,
  DORA/SPACE metrics, and team rituals with 10+ sections)
- Zero TODO/FIXME/XXX placeholders and zero secret leaks across all docs & scripts
- All 4 Chaos Mesh scripts in scripts/gameday/: bash -n, --dry-run, --cleanup --dry-run,
  and strict production-namespace safety guardrails
- grafana/dashboards/improvement-metrics.json DORA & GameDay panels + balanced PromQL
- .github/projects/kaizen-board.yml valid YAML structure with 5 columns & labels
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from product_value_system.security.secret_detector import HybridSecretDetector
from tests.monitoring.test_slo_definitions import _assert_balanced_promql

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "docs" / "operations"
SCENARIOS_DIR = OPS_DIR / "gameday-scenarios"
GAMEDAY_SCRIPTS_DIR = REPO_ROOT / "scripts" / "gameday"
DORA_DASHBOARD_PATH = REPO_ROOT / "grafana" / "dashboards" / "improvement-metrics.json"
KAIZEN_BOARD_PATH = REPO_ROOT / ".github" / "projects" / "kaizen-board.yml"
ONCALL_GUIDE_PATH = OPS_DIR / "oncall-guide.md"

IMPROVEMENT_GUIDES = (
    OPS_DIR / "gameday-facilitation-guide.md",
    OPS_DIR / "retrospective-template.md",
    OPS_DIR / "dr-drill-procedures.md",
    OPS_DIR / "improvement-metrics.md",
    OPS_DIR / "team-rituals.md",
)

GAMEDAY_SCRIPTS = (
    "inject-latency.sh",
    "kill-random-pod.sh",
    "partition-network.sh",
    "exhaust-memory.sh",
)

PLACEHOLDER_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")


def _find_bash() -> str | None:
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.exists():
        return str(git_bash)
    return shutil.which("bash")


@pytest.mark.unit
def test_fifteen_gameday_scenarios_and_catalog_complete() -> None:
    """At least 15 structured GameDay scenarios + README catalog exist with zero TODOs/secrets."""
    readme_path = SCENARIOS_DIR / "README.md"
    assert readme_path.exists(), f"Missing {readme_path}"
    readme_text = readme_path.read_text(encoding="utf-8")

    scenario_files = sorted(p for p in SCENARIOS_DIR.glob("*.md") if p.name != "README.md")
    assert len(scenario_files) >= 15, (
        f"Expected >= 15 GameDay scenarios, found {len(scenario_files)}"
    )

    detector = HybridSecretDetector()
    for path in scenario_files:
        text = path.read_text(encoding="utf-8")
        assert path.name in readme_text, f"gameday-scenarios/README.md missing {path.name}"
        assert not PLACEHOLDER_RE.search(text), f"Placeholder marker in {path.name}"
        assert detector.detect(text) == [], f"Unexpected secret finding in {path.name}"
        for required in (
            "**Scenario ID**:",
            "## Objective",
            "## Procedure",
            "## Cleanup",
            "## Expected Outcomes",
        ):
            assert required in text, f"{path.name} missing required section {required!r}"


@pytest.mark.unit
def test_continuous_improvement_guides_and_team_rituals_complete() -> None:
    """All 5 improvement guides exist cleanly; team-rituals.md has 10+ sections."""
    detector = HybridSecretDetector()
    for guide in IMPROVEMENT_GUIDES:
        assert guide.exists(), f"Missing {guide}"
        text = guide.read_text(encoding="utf-8")
        assert not PLACEHOLDER_RE.search(text), f"Placeholder marker in {guide.name}"
        assert detector.detect(text) == [], f"Unexpected secret finding in {guide.name}"

    rituals_text = (OPS_DIR / "team-rituals.md").read_text(encoding="utf-8")
    h2_headings = [line for line in rituals_text.splitlines() if line.startswith("## ")]
    assert len(h2_headings) >= 10, (
        f"Expected >= 10 '##' sections in team-rituals.md, found {len(h2_headings)}"
    )

    metrics_text = (OPS_DIR / "improvement-metrics.md").read_text(encoding="utf-8")
    assert "```mermaid" in metrics_text
    for token in (
        "Deployment Frequency",
        "Lead Time for Changes",
        "Change Failure Rate",
        "Mean Time To Recovery",
        "SPACE",
        "aggregate_only: true",
    ):
        assert token in metrics_text, f"improvement-metrics.md missing {token!r}"

    dr_text = (OPS_DIR / "dr-drill-procedures.md").read_text(encoding="utf-8")
    for token in ("RTO", "RPO", "restore-database.sh", "pitr-recovery.sh", "SOC2"):
        assert token in dr_text, f"dr-drill-procedures.md missing {token!r}"

    oncall_text = ONCALL_GUIDE_PATH.read_text(encoding="utf-8")
    for linked in (
        "team-rituals.md",
        "improvement-metrics.md",
        "gameday-facilitation-guide.md",
        "retrospective-template.md",
        "dr-drill-procedures.md",
    ):
        assert linked in oncall_text, f"oncall-guide.md missing link to {linked}"


@pytest.mark.unit
def test_chaos_gameday_scripts_syntax_dry_run_cleanup_and_prod_guard() -> None:
    """All 4 Chaos Mesh scripts pass bash -n, --dry-run, --cleanup, and block unapproved prod."""
    bash_bin = _find_bash()
    detector = HybridSecretDetector()

    for script_name in GAMEDAY_SCRIPTS:
        path = GAMEDAY_SCRIPTS_DIR / script_name
        assert path.exists(), f"Missing {path}"
        content = path.read_text(encoding="utf-8")
        assert content.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in content
        assert "chaos-mesh.org/v1alpha1" in content
        assert detector.detect(content) == [], f"Secret finding in {script_name}"

        if bash_bin is not None:
            syntax = subprocess.run(
                [bash_bin, "-n", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            assert syntax.returncode == 0, f"bash -n failed for {script_name}: {syntax.stderr}"

            # Default staging dry-run succeeds and prints Chaos Mesh CRD
            dry = subprocess.run(
                [bash_bin, str(path), "--dry-run"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert dry.returncode == 0, f"--dry-run failed for {script_name}: {dry.stderr}"
            assert "[DRY-RUN]" in dry.stdout
            assert "chaos-mesh.org/v1alpha1" in dry.stdout

            # Cleanup dry-run succeeds
            cleanup = subprocess.run(
                [bash_bin, str(path), "--cleanup", "--dry-run"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert cleanup.returncode == 0, f"--cleanup --dry-run failed: {cleanup.stderr}"
            assert "[DRY-RUN]" in cleanup.stdout

            # Production namespace blocked without explicit approval ticket
            blocked = subprocess.run(
                [bash_bin, str(path), "--namespace", "product-value-system", "--dry-run"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert blocked.returncode == 1, f"{script_name} must block unapproved prod namespace"
            assert "BLOCKED:" in blocked.stderr

            # Production namespace allowed only with explicit flag + ticket
            approved = subprocess.run(
                [
                    bash_bin,
                    str(path),
                    "--namespace",
                    "product-value-system",
                    "--allow-production",
                    "--approval-ticket",
                    "CHG-2026-042",
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert approved.returncode == 0, approved.stderr


@pytest.mark.unit
def test_dora_improvement_dashboard_and_kaizen_board_valid() -> None:
    """DORA Grafana dashboard (7 panels) & .github/projects/kaizen-board.yml are valid."""
    assert DORA_DASHBOARD_PATH.exists()
    dash = json.loads(DORA_DASHBOARD_PATH.read_text(encoding="utf-8"))
    assert dash["uid"] == "pvs-improvement-metrics"
    assert dash["schemaVersion"] >= 36
    assert len(dash["panels"]) >= 7

    titles = " ".join(p["title"] for p in dash["panels"])
    for expected in (
        "Deployment Frequency",
        "Lead Time",
        "Change Failure Rate",
        "MTTR",
        "DORA Performance Category",
        "GameDay Success Rate",
    ):
        assert expected in titles, f"Missing DORA panel matching {expected!r}"

    for panel in dash["panels"]:
        assert panel["targets"]
        for target in panel["targets"]:
            _assert_balanced_promql(target["expr"], panel["title"])

    assert KAIZEN_BOARD_PATH.exists()
    kaizen = yaml.safe_load(KAIZEN_BOARD_PATH.read_text(encoding="utf-8"))
    assert kaizen["name"] == "SRE Kaizen Board"
    col_names = [c["name"] for c in kaizen["columns"]]
    assert col_names == ["Backlog", "Prioritized", "In Progress", "Validating", "Done"]
    assert {
        "improvement",
        "tech-debt",
        "reliability",
        "performance",
        "observability",
        "automation",
    }.issubset(set(kaizen["issue_labels"]))
