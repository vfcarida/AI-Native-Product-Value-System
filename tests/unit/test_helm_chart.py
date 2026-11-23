"""Automated Kubernetes Helm chart schema, template, and security posture tests (v1.0)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest
import yaml

from product_value_system.security.secret_detector import HybridSecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART_DIR = REPO_ROOT / "deploy" / "helm" / "product-value-system"
TEMPLATES_DIR = CHART_DIR / "templates"


@pytest.mark.unit
def test_helm_chart_metadata_and_values_schema_validation() -> None:
    """Chart.yaml is valid Helm v2 and values.yaml conforms strictly to values.schema.json."""
    chart = yaml.safe_load((CHART_DIR / "Chart.yaml").read_text(encoding="utf-8"))
    assert chart["apiVersion"] == "v2"
    assert chart["name"] == "product-value-system"
    assert chart["type"] == "application"
    assert re.match(r"^\d+\.\d+\.\d+", str(chart["version"]))

    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))
    schema = json.loads((CHART_DIR / "values.schema.json").read_text(encoding="utf-8"))

    # Default values.yaml must pass schema validation cleanly
    jsonschema.validate(instance=values, schema=schema)

    # Insecure overrides must be rejected by values.schema.json
    insecure_priv = {
        **values,
        "securityContext": {**values["securityContext"], "allowPrivilegeEscalation": True},
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=insecure_priv, schema=schema)

    insecure_root = {
        **values,
        "podSecurityContext": {**values["podSecurityContext"], "runAsNonRoot": False},
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=insecure_root, schema=schema)


@pytest.mark.unit
def test_helm_chart_enforces_restricted_pss_probes_privacy_and_zero_secrets() -> None:
    """Verify Kubernetes Restricted PSS, health probes, privacy defaults, and zero secrets."""
    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))

    # Restricted Pod Security Standard invariants matching Dockerfile UID 10001
    assert values["serviceAccount"]["automountServiceAccountToken"] is False
    assert values["podSecurityContext"]["runAsNonRoot"] is True
    assert values["podSecurityContext"]["runAsUser"] == 10001
    assert values["podSecurityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert values["securityContext"]["allowPrivilegeEscalation"] is False
    assert values["securityContext"]["readOnlyRootFilesystem"] is True
    assert values["securityContext"]["capabilities"]["drop"] == ["ALL"]

    # Health probes & metrics scrape match product_value_system.api.health endpoints
    assert values["probes"]["liveness"]["path"] == "/health/live"
    assert values["probes"]["readiness"]["path"] == "/health/ready"
    assert values["serviceMonitor"]["path"] == "/metrics"

    # Privacy-preserving telemetry defaults
    assert values["config"]["otelCaptureMessageContent"] == "false"
    assert values["config"]["devxAggregateOnly"] == "true"
    assert values["config"]["devxAnonymize"] == "true"
    assert values["networkPolicy"]["enabled"] is True

    # Zero leaked secrets anywhere in the Helm chart
    detector = HybridSecretDetector()
    for chart_file in sorted(CHART_DIR.rglob("*")):
        if chart_file.is_file():
            findings = detector.detect(chart_file.read_text(encoding="utf-8"))
            assert findings == [], f"Unexpected secret finding in {chart_file}: {findings}"


@pytest.mark.unit
def test_helm_templates_complete_and_balanced_go_directives() -> None:
    """Every required K8s template exists and has balanced Go template block directives."""
    expected_templates = {
        "_helpers.tpl",
        "configmap.yaml",
        "cronjob-archive.yaml",
        "deployment.yaml",
        "grafana-dashboards-configmap.yaml",
        "hpa.yaml",
        "ingress.yaml",
        "networkpolicy.yaml",
        "NOTES.txt",
        "otel-collector.yaml",
        "pdb.yaml",
        "prometheusrule.yaml",
        "secret.yaml",
        "service.yaml",
        "serviceaccount.yaml",
        "servicemonitor.yaml",
        "vault-auth-serviceaccount.yaml",
    }
    actual_templates = {p.name for p in TEMPLATES_DIR.iterdir() if p.is_file()}
    assert expected_templates.issubset(actual_templates)

    open_re = re.compile(r"\{\{-?\s*(?:if|with|range|define|block)\b")
    end_re = re.compile(r"\{\{-?\s*end\b")

    for tmpl in sorted(TEMPLATES_DIR.iterdir()):
        if not tmpl.is_file():
            continue
        text = tmpl.read_text(encoding="utf-8")
        assert text.count("{{") == text.count("}}"), f"Unbalanced braces in {tmpl.name}"
        opens = len(open_re.findall(text))
        ends = len(end_re.findall(text))
        assert opens == ends, f"Unbalanced Go template blocks in {tmpl.name}: {opens} vs {ends}"

    # Verify Deployment references live ASGI entrypoint and tmpfs mounts for readOnlyRootFilesystem
    dep_text = (TEMPLATES_DIR / "deployment.yaml").read_text(encoding="utf-8")
    assert "checksum/config:" in dep_text
    assert "mountPath: /tmp" in dep_text
    assert "startupProbe:" in dep_text
    assert "livenessProbe:" in dep_text
    assert "readinessProbe:" in dep_text

    # Run `helm lint` and `helm template` when helm binary is installed on PATH
    helm_bin = shutil.which("helm")
    if helm_bin:
        lint = subprocess.run(
            [helm_bin, "lint", str(CHART_DIR)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert lint.returncode == 0, f"helm lint failed:\n{lint.stdout}\n{lint.stderr}"

        rendered = subprocess.run(
            [helm_bin, "template", "pvs-test", str(CHART_DIR)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert rendered.returncode == 0, f"helm template failed:\n{rendered.stderr}"
        docs = [d for d in yaml.safe_load_all(rendered.stdout) if d]
        kinds = {d["kind"] for d in docs}
        assert {"Deployment", "Service", "ConfigMap", "ServiceAccount", "NetworkPolicy"}.issubset(
            kinds
        )
