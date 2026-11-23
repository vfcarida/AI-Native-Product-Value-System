"""
Comprehensive tests for PROD-008 Alerting & On-Call Automation:
- AlertManager YAML configuration validity, receivers & secret-file hardening
- Severity routing (critical -> PagerDuty + Slack, warning -> Slack, info -> Email)
- Alert grouping & deduplication by (alertname, cluster, service)
- Inhibition rules (ServiceDown & NodeDown alert-storm suppression)
- Maintenance window silences (exact + regex matchers & expiration)
- Privacy redaction of sensitive data in alert payloads
- Runbook & dashboard URL annotations across all PrometheusRule alerts
- PagerDuty on-call rotation (3+ primary engineers, 15m/30m escalation)
- Grafana alert-metrics dashboard PromQL validation & silence-alert.sh verification
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from product_value_system.contracts.config_schemas import (
    PagerDutyScheduleConfigFile,
    validate_config_file,
)
from product_value_system.monitoring.alert_router import (
    Alert,
    AlertRoutingEngine,
    SilenceMatcher,
    sanitize_alert_payload,
)
from product_value_system.security.secret_detector import HybridSecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]
ALERTMANAGER_CONFIG = (
    REPO_ROOT / "deploy" / "helm" / "dependencies" / "alertmanager-config.yaml"
)
PROMETHEUS_RULE_TMPL = (
    REPO_ROOT / "deploy" / "helm" / "product-value-system" / "templates" / "prometheusrule.yaml"
)
PAGERDUTY_SCHEDULE = REPO_ROOT / "config" / "pagerduty-schedule.yaml"
ALERT_DASHBOARD = REPO_ROOT / "grafana" / "dashboards" / "alert-metrics.json"
SILENCE_SCRIPT = REPO_ROOT / "scripts" / "silence-alert.sh"


@pytest.fixture
def routing_engine() -> AlertRoutingEngine:
    return AlertRoutingEngine.from_config_yaml(ALERTMANAGER_CONFIG)


# ---------------------------------------------------------------------------
# 1. AlertManager Configuration Validity & Security Hardening
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_alertmanager_config_valid_yaml_and_uses_secret_files() -> None:
    """AlertManager ConfigMap parses cleanly and references mounted Secret files (no plaintext)."""
    docs = [d for d in yaml.safe_load_all(ALERTMANAGER_CONFIG.read_text(encoding="utf-8")) if d]
    assert len(docs) == 1
    assert docs[0]["kind"] == "ConfigMap"
    am_cfg = yaml.safe_load(docs[0]["data"]["alertmanager.yml"])

    assert am_cfg["global"]["resolve_timeout"] == "5m"
    assert am_cfg["global"]["pagerduty_url"] == "https://events.pagerduty.com/v2/enqueue"
    assert am_cfg["global"]["slack_api_url_file"].startswith("/etc/alertmanager/secrets/")
    assert am_cfg["route"]["group_by"] == ["alertname", "cluster", "service"]
    assert am_cfg["route"]["group_wait"] == "10s"
    assert am_cfg["route"]["group_interval"] == "5m"
    assert am_cfg["route"]["repeat_interval"] == "4h"

    receivers = {r["name"]: r for r in am_cfg["receivers"]}
    assert {
        "default",
        "pagerduty-critical",
        "slack-critical",
        "slack-warnings",
        "email-info",
    }.issubset(receivers.keys())

    pd_cfg = receivers["pagerduty-critical"]["pagerduty_configs"][0]
    assert pd_cfg["service_key_file"].startswith("/etc/alertmanager/secrets/")
    assert "runbook" in pd_cfg["details"]
    assert len(pd_cfg["links"]) >= 2

    # Zero leaked credentials in AlertManager config or silence script
    detector = HybridSecretDetector()
    for target in (ALERTMANAGER_CONFIG, SILENCE_SCRIPT, PAGERDUTY_SCHEDULE):
        findings = detector.detect(target.read_text(encoding="utf-8"))
        assert findings == [], f"Unexpected secret finding in {target}: {findings}"


# ---------------------------------------------------------------------------
# 2. Severity Routing (PagerDuty, Slack, Email)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_critical_alert_routes_to_pagerduty_and_slack(
    routing_engine: AlertRoutingEngine,
) -> None:
    """Critical alerts fan out to BOTH pagerduty-critical and slack-critical (continue=true)."""
    alert = Alert(
        labels={
            "severity": "critical",
            "alertname": "SLOAvailabilityCritical",
            "service": "api",
            "cluster": "prod-us-east",
        },
        annotations={
            "summary": "SLO availability critical breach",
            "runbook_url": "https://runbooks.internal/slo-availability",
        },
    )
    dispatched_channels: list[str] = []
    routing_engine.dispatch([alert], sender=lambda recv, _p: dispatched_channels.append(recv))
    assert dispatched_channels == ["pagerduty-critical", "slack-critical"]


@pytest.mark.unit
def test_warning_alert_routes_to_slack_only_no_page(
    routing_engine: AlertRoutingEngine,
) -> None:
    """Warning alerts route exclusively to slack-warnings without paging PagerDuty."""
    alert = Alert(
        labels={"severity": "warning", "alertname": "SLOLatencyP99High", "service": "api"},
        annotations={"summary": "p99 latency elevated"},
    )
    channels: list[str] = []
    routing_engine.dispatch([alert], sender=lambda recv, _p: channels.append(recv))
    assert channels == ["slack-warnings"]


@pytest.mark.unit
def test_info_and_unmatched_alerts_route_correctly(
    routing_engine: AlertRoutingEngine,
) -> None:
    """Info alerts route to email-info; unmatched severities fall back to default receiver."""
    info_alert = Alert(labels={"severity": "info", "alertname": "ConfigReloaded"})
    unknown_alert = Alert(labels={"severity": "debug", "alertname": "CustomProbe"})

    assert routing_engine.route_alert(info_alert) == ["email-info"]
    assert routing_engine.route_alert(unknown_alert) == ["default"]


# ---------------------------------------------------------------------------
# 3. Grouping, Deduplication & Inhibition Rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_alert_grouping_batches_duplicate_service_alerts(
    routing_engine: AlertRoutingEngine,
) -> None:
    """Multiple pod alerts sharing (alertname, cluster, service) batch into 1 notification."""
    alerts = [
        Alert(
            labels={
                "severity": "warning",
                "alertname": "PodMemoryHigh",
                "cluster": "prod-1",
                "service": "api",
                "instance": f"pod-{i}",
            },
            annotations={"summary": f"High memory on pod-{i}"},
        )
        for i in range(3)
    ]
    delivered: list[dict[str, Any]] = []
    routing_engine.dispatch(alerts, sender=lambda _r, payload: delivered.append(payload))

    assert len(delivered) == 1
    assert delivered[0]["receiver"] == "slack-warnings"
    assert delivered[0]["alert_count"] == 3


@pytest.mark.unit
def test_inhibition_rules_suppress_downstream_service_and_node_alerts(
    routing_engine: AlertRoutingEngine,
) -> None:
    """ServiceDown suppresses downstream service alerts; NodeDown suppresses pod alerts on node."""
    service_down = Alert(
        labels={
            "alertname": "ServiceDown",
            "severity": "critical",
            "service": "api",
            "cluster": "prod-1",
        }
    )
    downstream_latency = Alert(
        labels={
            "alertname": "SLOLatencyP99High",
            "severity": "warning",
            "service": "api",
            "cluster": "prod-1",
        }
    )
    other_service = Alert(
        labels={
            "alertname": "SLOLatencyP99High",
            "severity": "warning",
            "service": "worker",
            "cluster": "prod-1",
        }
    )
    remaining = routing_engine.apply_inhibitions(
        [service_down, downstream_latency, other_service]
    )
    remaining_names = {(a.alertname, a.labels.get("service")) for a in remaining}
    assert ("ServiceDown", "api") in remaining_names
    assert ("SLOLatencyP99High", "worker") in remaining_names
    assert ("SLOLatencyP99High", "api") not in remaining_names

    node_down = Alert(labels={"alertname": "NodeDown", "severity": "critical", "node": "node-a"})
    pod_same_node = Alert(
        labels={"alertname": "PodMemoryHigh", "severity": "warning", "node": "node-a"}
    )
    pod_other_node = Alert(
        labels={"alertname": "PodMemoryHigh", "severity": "warning", "node": "node-b"}
    )
    node_remaining = routing_engine.apply_inhibitions([node_down, pod_same_node, pod_other_node])
    assert len(node_remaining) == 2
    assert all(
        not (a.alertname == "PodMemoryHigh" and a.labels.get("node") == "node-a")
        for a in node_remaining
    )


# ---------------------------------------------------------------------------
# 4. Maintenance Window Silences & Privacy Sanitization
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_maintenance_silence_suppresses_matching_alerts_and_expires(
    routing_engine: AlertRoutingEngine,
) -> None:
    """Maintenance silences suppress matching alerts during window and resume after expiry."""
    start = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
    silence = routing_engine.create_silence(
        [SilenceMatcher(name="alertname", value=".*", is_regex=True)],
        duration=timedelta(hours=2),
        created_by="sre-oncall",
        comment="PostgreSQL upgrade window",
        starts_at=start,
    )
    api_payload = silence.to_api_payload()
    assert api_payload["silenceID"] == silence.silence_id
    assert api_payload["comment"] == "PostgreSQL upgrade window"

    alert = Alert(labels={"severity": "critical", "alertname": "SLOAvailabilityCritical"})
    sent_during: list[str] = []
    routing_engine.dispatch(
        [alert],
        sender=lambda r, _p: sent_during.append(r),
        now=start + timedelta(minutes=30),
    )
    assert sent_during == []

    # After silence expires -> delivers normally again
    sent_after: list[str] = []
    routing_engine.dispatch(
        [alert],
        sender=lambda r, _p: sent_after.append(r),
        now=start + timedelta(hours=3),
    )
    assert sent_after == ["pagerduty-critical", "slack-critical"]


@pytest.mark.unit
def test_alert_payload_sanitization_redacts_secrets_emails_and_prompts() -> None:
    """Alert notifications never leak API keys, emails, Bearer tokens, or prompt bodies."""
    alert = Alert(
        labels={"alertname": "ModelError", "severity": "warning", "api_key": "must-drop"},
        annotations={
            "summary": "Failed for user@example.com with sk-demo12345",
            "prompt": "Confidential user prompt text",
            "runbook_url": "https://runbooks.internal/model-error",
        },
    )
    clean = sanitize_alert_payload(alert)
    assert "api_key" not in clean["labels"]
    assert clean["annotations"]["prompt"] == "[REDACTED]"
    assert "user@example.com" not in clean["annotations"]["summary"]
    assert "sk-demo12345" not in clean["annotations"]["summary"]
    assert clean["annotations"]["runbook_url"] == "https://runbooks.internal/model-error"


# ---------------------------------------------------------------------------
# 5. PrometheusRule Runbooks, PagerDuty Schedule, Grafana Dashboard & Script
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_prometheus_alerts_have_runbook_and_dashboard_urls() -> None:
    """Every alert rule (including all critical alerts) defines runbook_url & dashboard_url."""
    from tests.monitoring.test_slo_definitions import _render_prometheus_rule

    values = yaml.safe_load(
        (REPO_ROOT / "deploy" / "helm" / "product-value-system" / "values.yaml").read_text(
            encoding="utf-8"
        )
    )
    rule_doc = yaml.safe_load(_render_prometheus_rule(values))
    critical_count = 0
    total_alerts = 0
    for group in rule_doc["spec"]["groups"]:
        for rule in group["rules"]:
            if "alert" not in rule:
                continue
            total_alerts += 1
            ann = rule["annotations"]
            assert ann.get("runbook_url"), f"Alert {rule['alert']} missing runbook_url"
            assert ann.get("dashboard_url"), f"Alert {rule['alert']} missing dashboard_url"
            if rule["labels"].get("severity") == "critical":
                critical_count += 1
                assert rule["labels"].get("team") == "platform"

    assert total_alerts >= 12
    assert critical_count >= 5


@pytest.mark.unit
def test_pagerduty_schedule_contract_enforces_3_plus_engineers_and_15m_escalation() -> None:
    """config/pagerduty-schedule.yaml validates strictly and enforces >=3 primary engineers."""
    cfg = validate_config_file(PAGERDUTY_SCHEDULE)
    assert isinstance(cfg, PagerDutyScheduleConfigFile)
    assert len(cfg.schedules) >= 2
    assert len(cfg.schedules[0].layers[0].users) >= 3
    assert len(cfg.schedules[1].layers[0].users) >= 2

    delays = [r.escalation_delay_in_minutes for r in cfg.escalation_policies[0].rules]
    assert delays == [0, 15, 30]
    assert cfg.services[0].incident_urgency_rule.urgency == "high"

    # Verify schema rejects under-staffed (<3 engineers) primary schedule
    raw = yaml.safe_load(PAGERDUTY_SCHEDULE.read_text(encoding="utf-8"))
    raw["schedules"][0]["layers"][0]["users"] = [{"email": "only1@example.com"}]
    with pytest.raises(ValidationError, match=">= 3 engineers"):
        PagerDutyScheduleConfigFile.model_validate(raw)


@pytest.mark.unit
def test_alert_metrics_grafana_dashboard_valid_json_and_promql() -> None:
    """grafana/dashboards/alert-metrics.json defines all 6 alert KPI panels with valid PromQL."""
    from tests.monitoring.test_slo_definitions import _assert_balanced_promql

    dash = json.loads(ALERT_DASHBOARD.read_text(encoding="utf-8"))
    assert dash["uid"] == "pvs-alert-metrics"
    assert len(dash["panels"]) == 6
    titles = " ".join(p["title"] for p in dash["panels"])
    for expected in ("Severity", "Fire Rate", "MTTA", "MTTR", "False Positive", "Top 10"):
        assert expected in titles
    for panel in dash["panels"]:
        for target in panel["targets"]:
            _assert_balanced_promql(target["expr"], panel["title"])


@pytest.mark.unit
def test_silence_script_and_oncall_docs_exist_and_avoid_hardcoded_webhooks() -> None:
    """scripts/silence-alert.sh and on-call docs exist cleanly without hardcoded webhook secrets."""
    script_text = SILENCE_SCRIPT.read_text(encoding="utf-8")
    assert script_text.startswith("#!/usr/bin/env bash")
    assert "/api/v2/silences" in script_text
    assert "SLACK_WEBHOOK_URL" in script_text
    assert "hooks.slack.com/services/" not in script_text

    oncall_doc = (REPO_ROOT / "docs" / "operations" / "oncall-guide.md").read_text(encoding="utf-8")
    index_doc = (REPO_ROOT / "docs" / "operations" / "alert-runbook-index.md").read_text(
        encoding="utf-8"
    )
    assert "PagerDuty" in oncall_doc and "silence-alert.sh" in oncall_doc
    assert "SLOAvailabilityCritical" in index_doc and "SLOLatencyP99Critical" in index_doc
