"""
Automated verification suite for PROD-012: Runbooks & Operational Playbooks.

Validates:
- >= 20 standardized incident runbooks with metadata, 7 required sections, and bash commands
- Zero TODO / FIXME / XXX placeholders in docs/runbooks/ or .github/RUNBOOK_TEMPLATE.md
- All 6 core operational playbooks & complete cross-indexing of all 20 incident runbooks
- All 4 runbook automation scripts: shebang, set -euo pipefail, bash -n syntax, --dry-run execution
- Zero secret leaks across all runbooks, playbooks, templates, and automation scripts
- 100% of PrometheusRule alerts link directly to existing docs/runbooks/incident-*.md files
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from product_value_system.security.secret_detector import HybridSecretDetector
from tests.monitoring.test_slo_definitions import _render_prometheus_rule

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOKS_DIR = REPO_ROOT / "docs" / "runbooks"
TEMPLATE_PATH = REPO_ROOT / ".github" / "RUNBOOK_TEMPLATE.md"
SCRIPTS_DIR = REPO_ROOT / "scripts" / "runbook-automation"
VALUES_PATH = REPO_ROOT / "deploy" / "helm" / "product-value-system" / "values.yaml"
ALERT_INDEX_PATH = REPO_ROOT / "docs" / "operations" / "alert-runbook-index.md"
ONCALL_GUIDE_PATH = REPO_ROOT / "docs" / "operations" / "oncall-guide.md"

REQUIRED_METADATA = (
    "**Severity**:",
    "**Alert**:",
    "**SLO Impact**:",
    "**Estimated MTTR**:",
    "**Last Updated**:",
    "**Owner**:",
)

REQUIRED_SECTIONS = (
    "## 1. Symptoms",
    "## 2. Diagnosis",
    "## 3. Remediation",
    "## 4. Prevention",
    "## 5. Escalation",
    "## 6. References",
    "## 7. Testing",
)

CORE_PLAYBOOKS = (
    "deployment-checklist.md",
    "rollback-procedure.md",
    "emergency-data-loss.md",
    "emergency-security-breach.md",
    "troubleshooting-flowchart.md",
    "oncall-quick-reference.md",
)

AUTOMATION_SCRIPTS = (
    "auto-scale-up.sh",
    "restart-unhealthy-pods.sh",
    "clear-cache.sh",
    "diagnose-slow-queries.sh",
)

PLACEHOLDER_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")
RUNBOOK_URL_RE = re.compile(r"/blob/main/(docs/runbooks/incident-[a-z0-9-]+\.md)$")


def _find_bash() -> str | None:
    """Locate a usable bash binary on Linux/macOS or Windows Git Bash."""
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.exists():
        return str(git_bash)
    return shutil.which("bash")


@pytest.mark.unit
def test_twenty_plus_incident_runbooks_exist_with_standard_7_section_structure() -> None:
    """At least 20 incident runbooks exist, each adhering to the 7-section operational standard."""
    incident_files = sorted(RUNBOOKS_DIR.glob("incident-*.md"))
    assert len(incident_files) >= 20, (
        f"Expected >= 20 incident runbooks, found {len(incident_files)}"
    )

    for runbook in incident_files:
        text = runbook.read_text(encoding="utf-8")
        for meta in REQUIRED_METADATA:
            assert meta in text, f"{runbook.name} missing metadata field {meta!r}"
        for heading in REQUIRED_SECTIONS:
            assert heading in text, f"{runbook.name} missing required section {heading!r}"
        assert "```bash" in text, f"{runbook.name} must include concrete bash diagnostic commands"


@pytest.mark.unit
def test_no_todo_fixme_xxx_markers_in_runbooks() -> None:
    """No unresolved TODO, FIXME, or XXX placeholders remain in runbooks or template."""
    targets = sorted(RUNBOOKS_DIR.glob("*.md")) + [TEMPLATE_PATH]
    offenders: list[str] = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if PLACEHOLDER_RE.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no}: {line.strip()}")
    assert not offenders, "Unresolved placeholders found:\n" + "\n".join(offenders)


@pytest.mark.unit
def test_core_operational_playbooks_and_template_complete() -> None:
    """All 6 core playbooks and .github/RUNBOOK_TEMPLATE.md exist and index all 20 incidents."""
    assert TEMPLATE_PATH.exists(), f"Missing {TEMPLATE_PATH}"
    template_text = TEMPLATE_PATH.read_text(encoding="utf-8")
    for heading in REQUIRED_SECTIONS:
        assert heading in template_text, f"RUNBOOK_TEMPLATE.md missing {heading!r}"

    for playbook_name in CORE_PLAYBOOKS:
        path = RUNBOOKS_DIR / playbook_name
        assert path.exists(), f"Missing core playbook {playbook_name}"
        assert path.stat().st_size > 500, f"Core playbook {playbook_name} appears incomplete"

    incident_names = [p.name for p in sorted(RUNBOOKS_DIR.glob("incident-*.md"))]
    quick_ref = (RUNBOOKS_DIR / "oncall-quick-reference.md").read_text(encoding="utf-8")
    flowchart = (RUNBOOKS_DIR / "troubleshooting-flowchart.md").read_text(encoding="utf-8")

    assert "```mermaid" in flowchart, "troubleshooting-flowchart.md must include a Mermaid diagram"
    for inc_name in incident_names:
        assert inc_name in quick_ref, f"oncall-quick-reference.md missing link to {inc_name}"
        assert inc_name in flowchart, f"troubleshooting-flowchart.md missing link to {inc_name}"


@pytest.mark.unit
def test_runbook_automation_scripts_syntax_dry_run_and_security() -> None:
    """All 4 automation scripts pass bash -n, execute --dry-run cleanly, and leak zero secrets."""
    bash_bin = _find_bash()
    detector = HybridSecretDetector()

    for script_name in AUTOMATION_SCRIPTS:
        script_path = SCRIPTS_DIR / script_name
        assert script_path.exists(), f"Missing automation script {script_path}"
        content = script_path.read_text(encoding="utf-8")
        assert content.startswith("#!/usr/bin/env bash"), f"{script_name} missing bash shebang"
        assert "set -euo pipefail" in content, f"{script_name} missing strict mode"
        assert "--dry-run" in content, f"{script_name} must support --dry-run"

        findings = detector.detect(content)
        assert findings == [], f"Secret finding in {script_name}: {findings}"

        if bash_bin is not None:
            syntax = subprocess.run(
                [bash_bin, "-n", str(script_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            assert syntax.returncode == 0, f"bash -n failed for {script_name}: {syntax.stderr}"

            dry_run = subprocess.run(
                [bash_bin, str(script_path), "--dry-run"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert dry_run.returncode == 0, (
                f"--dry-run failed for {script_name}: "
                f"stdout={dry_run.stdout} stderr={dry_run.stderr}"
            )
            assert "[DRY-RUN]" in dry_run.stdout, f"{script_name} --dry-run missing [DRY-RUN] tag"


@pytest.mark.unit
def test_zero_secrets_across_all_runbooks_and_playbooks() -> None:
    """HybridSecretDetector finds zero exposed credentials in docs/runbooks/ and template."""
    detector = HybridSecretDetector()
    for md_path in sorted(RUNBOOKS_DIR.glob("*.md")) + [TEMPLATE_PATH]:
        findings = detector.detect(md_path.read_text(encoding="utf-8"))
        assert findings == [], f"Unexpected secret finding in {md_path.name}: {findings}"


@pytest.mark.unit
def test_all_prometheus_alerts_link_to_existing_incident_runbooks() -> None:
    """Every PrometheusRule alert links directly to an existing docs/runbooks/incident-*.md file."""
    values = yaml.safe_load(VALUES_PATH.read_text(encoding="utf-8"))
    rule_doc = yaml.safe_load(_render_prometheus_rule(values))
    index_text = ALERT_INDEX_PATH.read_text(encoding="utf-8")
    oncall_text = ONCALL_GUIDE_PATH.read_text(encoding="utf-8")

    alert_count = 0
    for group in rule_doc["spec"]["groups"]:
        for rule in group["rules"]:
            if "alert" not in rule:
                continue
            alert_count += 1
            alert_name = rule["alert"]
            runbook_url = str(rule["annotations"].get("runbook_url", ""))
            match = RUNBOOK_URL_RE.search(runbook_url)
            assert match is not None, (
                f"Alert {alert_name} runbook_url must link to an incident runbook: {runbook_url}"
            )
            rel_path = match.group(1)
            assert (REPO_ROOT / rel_path).exists(), (
                f"Alert {alert_name} references missing runbook {rel_path}"
            )
            assert alert_name in index_text, (
                f"Alert {alert_name} missing from docs/operations/alert-runbook-index.md"
            )

    assert alert_count >= 15, f"Expected >= 15 Prometheus alerts, found {alert_count}"

    for playbook_name in CORE_PLAYBOOKS:
        assert playbook_name in oncall_text, (
            f"docs/operations/oncall-guide.md missing link to {playbook_name}"
        )
