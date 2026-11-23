"""
Automated validation suite for PROD-013: Incident Management Process.

Validates:
- All 5 incident management operational guides & templates + GitHub issue template
- Blameless postmortem template completeness (200+ lines, 5 Whys, timeline, action items)
- Zero TODO/FIXME/XXX placeholders and zero secret leaks across docs and scripts
- All 3 incident management automation scripts: bash -n syntax, --dry-run, input validation
- Grafana incident-metrics.json schema, all 6 panels, and balanced PromQL
- PrometheusRule alert rules carry both `severity` and `incident_severity` (P0-P4)
- PagerDuty schedule defines the Incident Commander (IC) rotation
- IncidentManager lifecycle, SLA evaluation, privacy sanitization, and Prometheus metrics export
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from product_value_system.contracts.config_schemas import (
    PagerDutyScheduleConfigFile,
    validate_config_file,
)
from product_value_system.monitoring.incident_manager import (
    SEVERITY_POLICIES,
    ActionItem,
    IncidentManager,
    IncidentSeverity,
    IncidentStatus,
    classify_alert_severity,
    sanitize_incident_text,
)
from product_value_system.security.secret_detector import HybridSecretDetector
from tests.monitoring.test_slo_definitions import (
    _assert_balanced_promql,
    _render_prometheus_rule,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "docs" / "operations"
ISSUE_TEMPLATE_PATH = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "incident-report.md"
SCRIPTS_DIR = REPO_ROOT / "scripts" / "incident-management"
INCIDENT_DASHBOARD_PATH = REPO_ROOT / "grafana" / "dashboards" / "incident-metrics.json"
VALUES_PATH = REPO_ROOT / "deploy" / "helm" / "product-value-system" / "values.yaml"
PAGERDUTY_SCHEDULE_PATH = REPO_ROOT / "config" / "pagerduty-schedule.yaml"

INCIDENT_DOCS = (
    OPS_DIR / "incident-management.md",
    OPS_DIR / "incident-severity-guide.md",
    OPS_DIR / "incident-commander-guide.md",
    OPS_DIR / "postmortem-template.md",
    OPS_DIR / "communication-templates.md",
    ISSUE_TEMPLATE_PATH,
)

PLACEHOLDER_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")


def _find_bash() -> str | None:
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.exists():
        return str(git_bash)
    return shutil.which("bash")


@pytest.mark.unit
def test_incident_management_docs_and_issue_template_complete() -> None:
    """All 5 incident docs and GitHub issue template exist with required sections & zero TODOs."""
    detector = HybridSecretDetector()
    for doc_path in INCIDENT_DOCS:
        assert doc_path.exists(), f"Missing {doc_path}"
        text = doc_path.read_text(encoding="utf-8")
        assert not PLACEHOLDER_RE.search(text), f"Placeholder marker found in {doc_path}"
        findings = detector.detect(text)
        assert findings == [], f"Unexpected secret finding in {doc_path.name}: {findings}"

    # Severity guide defines P0..P4 and upgrade/downgrade authority
    sev_text = (OPS_DIR / "incident-severity-guide.md").read_text(encoding="utf-8")
    for token in ("P0", "P1", "P2", "P3", "P4", "Upgrade", "Downgrade", "IC Authority"):
        assert token in sev_text, f"incident-severity-guide.md missing {token!r}"

    # IC guide defines role, rotation, authority, checklist, and worked timeline
    ic_text = (OPS_DIR / "incident-commander-guide.md").read_text(encoding="utf-8")
    for token in ("Role Purpose", "IC Rotation", "Decision Authority", "IC Operational Checklist"):
        assert token in ic_text, f"incident-commander-guide.md missing {token!r}"

    # Postmortem template has >= 200 lines and blameless 5 Whys structure
    pm_lines = (OPS_DIR / "postmortem-template.md").read_text(encoding="utf-8").splitlines()
    assert len(pm_lines) >= 200, (
        f"Expected >= 200 lines in postmortem-template.md, got {len(pm_lines)}"
    )
    pm_text = "\n".join(pm_lines)
    for token in ("Blameless", "Executive Summary", "5 Whys", "Timeline", "Action Items"):
        assert token in pm_text, f"postmortem-template.md missing {token!r}"

    # Communication templates cover Slack, Status Page, Email, and Tracker tickets
    comm_text = (OPS_DIR / "communication-templates.md").read_text(encoding="utf-8")
    for token in ("Slack", "Status Page", "Stakeholder Email", "Investigating", "Resolved"):
        assert token in comm_text, f"communication-templates.md missing {token!r}"

    # GitHub Issue Template YAML frontmatter
    issue_text = ISSUE_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert issue_text.startswith("---\n")
    assert "name: Incident Report" in issue_text
    assert 'labels: ["incident"]' in issue_text


@pytest.mark.unit
def test_incident_automation_scripts_syntax_dry_run_and_validation() -> None:
    """All 3 incident management scripts pass bash -n, --dry-run, invalid-input & secret checks."""
    bash_bin = _find_bash()
    detector = HybridSecretDetector()
    expected_scripts = (
        "create-incident.sh",
        "update-status-page.sh",
        "notify-stakeholders.sh",
    )

    for script_name in expected_scripts:
        script_path = SCRIPTS_DIR / script_name
        assert script_path.exists(), f"Missing {script_path}"
        content = script_path.read_text(encoding="utf-8")
        assert content.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in content
        assert "hooks.slack.com/services/" not in content
        assert detector.detect(content) == [], f"Secret finding in {script_name}"

        if bash_bin is not None:
            syntax = subprocess.run(
                [bash_bin, "-n", str(script_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            assert syntax.returncode == 0, f"bash -n failed for {script_name}: {syntax.stderr}"

    if bash_bin is not None:
        # 1. Positional invocation matching PROD-013 Verification Command #4 (with --dry-run)
        create_res = subprocess.run(
            [
                bash_bin,
                str(SCRIPTS_DIR / "create-incident.sh"),
                "Test Incident",
                "P2",
                "Testing automation",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert create_res.returncode == 0, create_res.stderr
        assert "[DRY-RUN]" in create_res.stdout
        assert "severity:P2" in create_res.stdout

        # Invalid severity rejected cleanly
        bad_sev = subprocess.run(
            [
                bash_bin,
                str(SCRIPTS_DIR / "create-incident.sh"),
                "Test Incident",
                "P9",
                "Invalid",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert bad_sev.returncode == 2

        # 2. Status page dry-run
        status_res = subprocess.run(
            [
                bash_bin,
                str(SCRIPTS_DIR / "update-status-page.sh"),
                "--incident-id",
                "INC-20261123-0200",
                "--severity",
                "P1",
                "--status",
                "investigating",
                "--title",
                "API Latency Degradation",
                "--message",
                "Investigating elevated p99 latency.",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert status_res.returncode == 0, status_res.stderr
        assert "[DRY-RUN]" in status_res.stdout
        assert '"impact_override": "major"' in status_res.stdout

        # 3. Stakeholder notification dry-run
        notify_res = subprocess.run(
            [
                bash_bin,
                str(SCRIPTS_DIR / "notify-stakeholders.sh"),
                "--incident-id",
                "INC-20261123-0200",
                "--severity",
                "P0",
                "--status",
                "investigating",
                "--ic",
                "alice",
                "--title",
                "API Unavailable",
                "--summary",
                "Failing over database primary.",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert notify_res.returncode == 0, notify_res.stderr
        assert "[DRY-RUN]" in notify_res.stdout
        assert "Executive Leadership" in notify_res.stdout


@pytest.mark.unit
def test_grafana_incident_metrics_dashboard_valid_json_and_promql() -> None:
    """grafana/dashboards/incident-metrics.json defines 6 incident KPI panels with valid PromQL."""
    assert INCIDENT_DASHBOARD_PATH.exists()
    dash = json.loads(INCIDENT_DASHBOARD_PATH.read_text(encoding="utf-8"))
    assert dash["uid"] == "pvs-incident-metrics"
    assert dash["schemaVersion"] >= 36
    assert len(dash["panels"]) == 6

    titles = " ".join(p["title"] for p in dash["panels"])
    for expected in (
        "Severity",
        "MTTR",
        "Frequency",
        "Categories",
        "Postmortem Completion",
        "Improvement",
    ):
        assert expected in titles, f"Missing panel matching {expected!r}"

    for panel in dash["panels"]:
        assert panel["targets"], f"Panel {panel['title']} has no targets"
        for target in panel["targets"]:
            _assert_balanced_promql(target["expr"], panel["title"])


@pytest.mark.unit
def test_prometheus_rules_have_incident_severity_and_pagerduty_has_ic_schedule() -> None:
    """All 15+ PrometheusRule alerts define incident_severity (P0-P4) and PagerDuty defines IC."""
    values = yaml.safe_load(VALUES_PATH.read_text(encoding="utf-8"))
    rule_doc = yaml.safe_load(_render_prometheus_rule(values))

    valid_inc_sevs = {s.value for s in IncidentSeverity}
    seen_inc_sevs: set[str] = set()
    alert_count = 0
    for group in rule_doc["spec"]["groups"]:
        for rule in group["rules"]:
            if "alert" not in rule:
                continue
            alert_count += 1
            labels = rule["labels"]
            inc_sev = labels.get("incident_severity")
            assert inc_sev in valid_inc_sevs, (
                f"Alert {rule['alert']} missing valid incident_severity label: {labels}"
            )
            seen_inc_sevs.add(inc_sev)
            # classify_alert_severity matches declared label
            assert classify_alert_severity(labels) == IncidentSeverity(inc_sev)

    assert alert_count >= 15
    assert {"P0", "P1", "P2", "P3"}.issubset(seen_inc_sevs)

    # PagerDuty schedule validates strictly and includes IC rotation
    pd_cfg = validate_config_file(PAGERDUTY_SCHEDULE_PATH)
    assert isinstance(pd_cfg, PagerDutyScheduleConfigFile)
    schedule_names = [s.name for s in pd_cfg.schedules]
    assert any("Incident Commander" in name for name in schedule_names), (
        f"Missing Incident Commander schedule in {schedule_names}"
    )


@pytest.mark.unit
def test_incident_manager_lifecycle_slas_redaction_and_prometheus_export() -> None:
    """IncidentManager tracks P0-P4 lifecycle, MTTR SLAs, postmortem rate, and redacts PII/keys."""
    mgr = IncidentManager()
    t0 = datetime(2026, 11, 23, 2, 0, tzinfo=UTC)

    # Declare P1 database failover incident (example from IC guide: 13 min MTTR)
    synthetic_bearer = "Bear" + "er eyJhbGciOiJIUzI1NiJ9"
    inc = mgr.declare_incident(
        title=f"PostgreSQL primary OOM for ops@example.com with {synthetic_bearer}",
        severity=IncidentSeverity.P1,
        category="database",
        started_at=t0,
    )
    assert "ops@example.com" not in inc.title
    assert "eyJhbGciOiJIUzI1NiJ9" not in inc.title
    assert inc.policy == SEVERITY_POLICIES[IncidentSeverity.P1]
    assert inc.policy.postmortem_required is True

    mgr.acknowledge(inc.incident_id, incident_commander="bob", at=t0 + timedelta(minutes=3))
    mgr.add_timeline_event(
        inc.incident_id,
        actor="alice",
        message="Patroni promoted standby postgresql-ha-1",
        status=IncidentStatus.MONITORING,
        at=t0 + timedelta(minutes=9),
    )
    mgr.resolve(
        inc.incident_id,
        actor="bob",
        summary="SLOs green; primary recovered",
        at=t0 + timedelta(minutes=13),
    )

    assert inc.mtta_seconds == pytest.approx(180.0)
    assert inc.mttr_seconds == pytest.approx(780.0)
    assert inc.ack_sla_met() is True
    assert inc.mttr_sla_met() is True
    assert mgr.postmortem_completion_rate() == pytest.approx(0.0)

    mgr.complete_postmortem(
        inc.incident_id,
        postmortem_url="https://internal.wiki/postmortems/INC-001",
        action_items=[
            ActionItem(
                action_id="ACT-1",
                priority="P0",
                description="Increase PostgreSQL memory limit 2Gi -> 8Gi",
                owner="dave",
                due_date="2026-11-24",
                ticket_ref="ENG-420",
            )
        ],
    )
    assert mgr.postmortem_completion_rate() == pytest.approx(100.0)

    # Severity upgrade P2 -> P1 logged in timeline
    inc2 = mgr.declare_incident(
        title="Elevated latency",
        severity=IncidentSeverity.P2,
        category="latency",
        started_at=t0,
    )
    mgr.change_severity(
        inc2.incident_id,
        new_severity=IncidentSeverity.P1,
        reason="p99 exceeded 5s across both zones",
        actor="alice",
        at=t0 + timedelta(minutes=5),
    )
    assert inc2.severity == IncidentSeverity.P1
    assert any("P2 -> P1" in e.message for e in inc2.timeline)
    assert mgr.postmortem_completion_rate() == pytest.approx(50.0)

    # Prometheus export matches Grafana dashboard metric names
    prom_text = mgr.export_prometheus_metrics()
    assert 'incidents_total{severity="P1",category="database"} 1' in prom_text
    assert 'incident_duration_seconds_bucket{severity="P1",le="900"} 1' in prom_text
    assert 'postmortems_completed_total{severity="P1"} 1' in prom_text

    # Direct sanitization helper redacts credentials
    redacted = sanitize_incident_text("password=supersecret user@corp.io")
    assert "supersecret" not in redacted
    assert "user@corp.io" not in redacted
