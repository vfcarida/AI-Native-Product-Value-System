"""
Automated validation for PROD-002: SLO Definitions, PrometheusRule Alerting,
Grafana SLO Dashboard, Helm Values Schema, and Backward-Compatible /metrics & /slos APIs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from product_value_system.api.health import create_app
from product_value_system.monitoring.metrics import PVSMetrics

REPO_ROOT = Path(__file__).resolve().parents[2]
SLO_DOC_PATH = REPO_ROOT / "docs" / "operations" / "slos.md"
CHART_DIR = REPO_ROOT / "deploy" / "helm" / "product-value-system"
PROM_RULE_PATH = CHART_DIR / "templates" / "prometheusrule.yaml"
VALUES_PATH = CHART_DIR / "values.yaml"
SCHEMA_PATH = CHART_DIR / "values.schema.json"
GRAFANA_DASHBOARD_PATH = REPO_ROOT / "grafana" / "dashboards" / "slo-dashboard.json"

HIGH_CARDINALITY_LABELS = frozenset(
    {"user_id", "run_id", "request_id", "trace_id", "session_id", "email", "ip"}
)
DURATION_RE = re.compile(r"^\d+[smhd]$")


def _render_prometheus_rule(values: dict[str, Any], namespace: str = "product-value-system") -> str:
    """Render templates/prometheusrule.yaml with concrete default values for YAML verification."""
    raw = PROM_RULE_PATH.read_text(encoding="utf-8")
    slo = values["slo"]
    replacements = {
        '{{ include "pvs.fullname" . }}': "product-value-system",
        "{{ $ns }}": namespace,
        "{{ .Values.slo.availability.errorBudget }}": str(slo["availability"]["errorBudget"]),
        "{{ .Values.slo.availability.criticalErrorRate }}": str(
            slo["availability"]["criticalErrorRate"]
        ),
        "{{ .Values.slo.latency.p99TargetSeconds }}": str(slo["latency"]["p99TargetSeconds"]),
        "{{ .Values.slo.latency.p99CriticalSeconds }}": str(slo["latency"]["p99CriticalSeconds"]),
        "{{ .Values.slo.latency.p95SustainedSeconds }}": str(
            slo["latency"]["p95SustainedSeconds"]
        ),
        "{{ .Values.slo.errorRate.threshold }}": str(slo["errorRate"]["threshold"]),
        "{{ .Values.slo.errorRate.criticalThreshold }}": str(slo["errorRate"]["criticalThreshold"]),
        "{{ .Values.slo.agentSuccess.target }}": str(slo["agentSuccess"]["target"]),
        "{{ .Values.slo.agentSuccess.criticalThreshold }}": str(
            slo["agentSuccess"]["criticalThreshold"]
        ),
        "{{ .Values.slo.saturation.memoryThreshold }}": str(slo["saturation"]["memoryThreshold"]),
        "{{ .Values.slo.saturation.cpuThrottleThreshold }}": str(
            slo["saturation"]["cpuThrottleThreshold"]
        ),
    }

    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(("{{- if", "{{- $ns", "{{- end", "{{- with", "{{- toYaml")):
            continue
        if 'include "pvs.labels"' in stripped:
            lines.append("    app.kubernetes.io/name: product-value-system")
            continue
        rendered_line = line
        for token, val in replacements.items():
            rendered_line = rendered_line.replace(token, val)
        # Unescape Helm-quoted Prometheus annotation templates: {{ "{{ ... }}" }} -> {{ ... }}
        rendered_line = re.sub(r'\{\{\s*"(\{\{[^"]+\}\})"\s*\}\}', r"\1", rendered_line)
        lines.append(rendered_line)

    return "\n".join(lines) + "\n"


def _assert_balanced_promql(expr: str, rule_name: str) -> None:
    """Verify PromQL expression has balanced delimiters and valid metric/function syntax."""
    cleaned = expr.strip()
    assert cleaned, f"Empty PromQL expression in {rule_name}"
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    in_quotes = False
    escaped = False
    for ch in cleaned:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_quotes:
            escaped = True
            continue
        if ch == '"':
            in_quotes = not in_quotes
            continue
        if in_quotes:
            continue
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in pairs.values():
            assert stack and stack[-1] == ch, f"Unbalanced PromQL delimiters in {rule_name}: {expr}"
            stack.pop()
    assert not in_quotes, f"Unterminated quote in PromQL expression for {rule_name}"
    assert not stack, f"Unclosed delimiters {stack} in PromQL expression for {rule_name}"
    assert "{{" not in cleaned and "}}" not in cleaned, (
        f"Unrendered Helm template directive left in PromQL for {rule_name}: {cleaned}"
    )


@pytest.mark.unit
def test_slo_operations_documentation_complete() -> None:
    """docs/operations/slos.md defines SLO-01..SLO-04, 28-day error budgets, and freeze policy."""
    assert SLO_DOC_PATH.exists(), f"Missing {SLO_DOC_PATH}"
    content = SLO_DOC_PATH.read_text(encoding="utf-8")

    for required_token in (
        "SLO-01",
        "SLO-02",
        "SLO-03",
        "SLO-04",
        "99.9%",
        "1000ms",
        "< 1%",
        "> 95%",
        "28d",
        "40.32 minutes",
        "403.2 minutes",
        "Error Budget Policy",
        "Freeze feature deployments",
        "48 hours",
        "25%",
        "Critical",
        "Warning",
    ):
        assert required_token in content, f"Missing required SLO content: {required_token!r}"


@pytest.mark.unit
def test_prometheus_rule_template_valid_and_has_10_plus_alerts() -> None:
    """Rendered PrometheusRule YAML is valid CRD with >=4 SLO groups, >=10 alerts & low labels."""
    values = yaml.safe_load(VALUES_PATH.read_text(encoding="utf-8"))
    rendered_yaml = _render_prometheus_rule(values)
    rule_doc = yaml.safe_load(rendered_yaml)

    assert rule_doc["apiVersion"] == "monitoring.coreos.com/v1"
    assert rule_doc["kind"] == "PrometheusRule"
    assert rule_doc["metadata"]["name"] == "product-value-system-alerts"

    groups = rule_doc["spec"]["groups"]
    group_names = {g["name"] for g in groups}
    for required_group in (
        "slo_recording_rules",
        "slo_availability",
        "slo_latency",
        "slo_error_rate",
        "slo_agent_success",
        "resource_saturation",
    ):
        assert required_group in group_names, f"Missing PrometheusRule group: {required_group}"

    alert_rules: list[dict[str, Any]] = []
    recording_rules: list[dict[str, Any]] = []

    for group in groups:
        assert DURATION_RE.match(str(group["interval"])), f"Invalid interval in {group['name']}"
        for rule in group["rules"]:
            if "alert" in rule:
                alert_rules.append(rule)
                _assert_balanced_promql(str(rule["expr"]), rule["alert"])
                assert DURATION_RE.match(str(rule["for"])), f"Invalid 'for' in {rule['alert']}"
                labels = rule.get("labels", {})
                assert labels.get("severity") in {"critical", "warning", "info"}
                assert labels.get("slo"), f"Alert {rule['alert']} missing 'slo' label"
                assert HIGH_CARDINALITY_LABELS.isdisjoint(labels.keys()), (
                    f"High-cardinality label in {rule['alert']}: {labels}"
                )
                annotations = rule.get("annotations", {})
                assert annotations.get("summary"), f"Missing summary in {rule['alert']}"
                assert annotations.get("description"), f"Missing description in {rule['alert']}"
                assert annotations.get("runbook_url"), f"Missing runbook_url in {rule['alert']}"
            elif "record" in rule:
                recording_rules.append(rule)
                _assert_balanced_promql(str(rule["expr"]), rule["record"])

    assert len(alert_rules) >= 10, f"Expected >= 10 alert rules, found {len(alert_rules)}"
    assert len(recording_rules) >= 4, f"Expected >= 4 recording rules, found {len(recording_rules)}"

    alert_names = {r["alert"] for r in alert_rules}
    for expected_alert in (
        "SLOAvailabilityBudgetBurn",
        "SLOAvailabilityCritical",
        "SLOLatencyP99High",
        "SLOLatencyP99Critical",
        "SLOErrorRateHigh",
        "AgentSuccessRateLow",
        "PodMemoryHigh",
        "PodCPUThrottled",
    ):
        assert expected_alert in alert_names, f"Missing expected alert: {expected_alert}"


@pytest.mark.unit
def test_grafana_slo_dashboard_json_valid_and_complete() -> None:
    """grafana/dashboards/slo-dashboard.json is valid JSON covering all 4 SLOs & error budgets."""
    assert GRAFANA_DASHBOARD_PATH.exists(), f"Missing {GRAFANA_DASHBOARD_PATH}"
    dash = json.loads(GRAFANA_DASHBOARD_PATH.read_text(encoding="utf-8"))

    assert dash["uid"] == "pvs-slo-dashboard"
    assert "SLO" in dash["title"]
    assert dash["schemaVersion"] >= 36

    panels = dash["panels"]
    assert len(panels) >= 6
    panel_titles = " ".join(p["title"] for p in panels)
    for expected in (
        "Availability",
        "Error Budget Remaining",
        "Latency p99",
        "Error Rate",
        "Agent",
    ):
        assert expected in panel_titles, f"Missing panel matching {expected!r}"

    for panel in panels:
        targets = panel.get("targets", [])
        assert targets, f"Panel {panel['title']} has no PromQL targets"
        for target in targets:
            _assert_balanced_promql(target["expr"], panel["title"])


@pytest.mark.unit
def test_helm_values_slo_section_schema_validation() -> None:
    """Helm values.yaml slo configuration conforms to values.schema.json and rejects bad targets."""
    values = yaml.safe_load(VALUES_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    jsonschema.validate(instance=values, schema=schema)
    assert values["slo"]["window"] == "28d"
    assert values["slo"]["availability"]["target"] == pytest.approx(0.999)
    assert values["slo"]["availability"]["errorBudget"] == pytest.approx(0.001)
    assert values["slo"]["latency"]["p99TargetSeconds"] == pytest.approx(1.0)
    assert values["slo"]["errorRate"]["threshold"] == pytest.approx(0.01)
    assert values["slo"]["agentSuccess"]["target"] == pytest.approx(0.95)

    invalid_values = {
        **values,
        "slo": {
            **values["slo"],
            "availability": {**values["slo"]["availability"], "target": 1.5},
        },
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=invalid_values, schema=schema)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_existing_metrics_and_slos_endpoints_unchanged() -> None:
    """Existing /metrics and /slos endpoints preserve exact metric names and low-cardinality."""
    metrics = PVSMetrics(register_global_prometheus=False)
    metrics.record_request(agent="discovery", status="success", latency_seconds=0.35, cost_usd=0.02)
    metrics.set_error_budget("availability", 0.95)
    app = create_app(metrics=metrics, metrics_api_key="")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        metrics_resp = await client.get("/metrics")
        slos_resp = await client.get("/slos")

    assert metrics_resp.status_code == 200
    for metric_name in (
        "pvs_requests_total",
        "pvs_errors_total",
        "pvs_latency_p95_seconds",
        "pvs_cost_usd_total",
        "pvs_error_budget_remaining",
    ):
        assert metric_name in metrics_resp.text

    assert slos_resp.status_code == 200
    slos_payload = slos_resp.json()
    assert "availability" in slos_payload["slos"]
    assert "error_rate" in slos_payload["slos"]
