"""
Automated validation suite for PROD-014: CI/CD Pipeline Hardening.

Validates:
- Hardened GitHub Actions workflows (security-scan.yml, deploy-hardened.yml,
  smoke-tests.yml, ci.yml) with Trivy + Snyk + CodeQL SAST gates, staging -> prod
  approvals, freeze checks, validation, and automatic rollback on failure
- .github/branch-protection.json & .github/CODEOWNERS enforcement rules
- All 4 deployment scripts (smoke-tests.sh, deployment-validation.sh,
  auto-rollback.sh, check-freeze.sh): bash -n, --dry-run, freeze windows, secrets
- LIVE end-to-end execution of scripts/deployment/smoke-tests.sh against both a
  healthy and a degraded ephemeral local FastAPI server (create_app())
- grafana/dashboards/deployment-metrics.json JSON schema & balanced PromQL
- docs/operations/deployment-process.md & deployment-freeze-calendar.md
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn
import yaml

from product_value_system.api.health import create_app
from product_value_system.monitoring.metrics import PVSMetrics
from product_value_system.security.secret_detector import HybridSecretDetector
from tests.monitoring.test_slo_definitions import _assert_balanced_promql

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
BRANCH_PROTECTION_PATH = REPO_ROOT / ".github" / "branch-protection.json"
CODEOWNERS_PATH = REPO_ROOT / ".github" / "CODEOWNERS"
DEPLOY_SCRIPTS_DIR = REPO_ROOT / "scripts" / "deployment"
DEPLOY_DASHBOARD_PATH = REPO_ROOT / "grafana" / "dashboards" / "deployment-metrics.json"
DEPLOY_PROCESS_DOC = REPO_ROOT / "docs" / "operations" / "deployment-process.md"
FREEZE_CALENDAR_DOC = REPO_ROOT / "docs" / "operations" / "deployment-freeze-calendar.md"

PLACEHOLDER_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")


def _find_bash() -> str | None:
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.exists():
        return str(git_bash)
    return shutil.which("bash")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_ephemeral_server(app: object) -> tuple[uvicorn.Server, threading.Thread, str]:
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")  # type: ignore[arg-type]
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health/live", timeout=1):
                return server, thread, base_url
        except Exception:
            time.sleep(0.05)
    server.should_exit = True
    raise RuntimeError(f"Ephemeral FastAPI server failed to start on {base_url}")


@pytest.mark.unit
def test_security_scan_and_deploy_hardened_and_smoke_workflows_valid() -> None:
    """All PROD-014 GitHub Actions workflows parse cleanly and enforce required gates."""
    sec_path = WORKFLOWS_DIR / "security-scan.yml"
    dep_path = WORKFLOWS_DIR / "deploy-hardened.yml"
    smoke_path = WORKFLOWS_DIR / "smoke-tests.yml"
    ci_path = WORKFLOWS_DIR / "ci.yml"

    for wf in (sec_path, dep_path, smoke_path, ci_path):
        assert wf.exists(), f"Missing workflow {wf}"

    # 1. security-scan.yml supports workflow_call and includes CodeQL SAST
    sec_raw = sec_path.read_text(encoding="utf-8")
    sec_doc = yaml.safe_load(sec_raw)
    sec_on = sec_doc.get("on") or sec_doc.get(True)
    assert "workflow_call" in sec_on
    assert "sast-scan" in sec_doc["jobs"]
    assert "github/codeql-action/init@v3" in sec_raw
    assert "github/codeql-action/analyze@v3" in sec_raw

    # 2. deploy-hardened.yml defines 7-stage gated pipeline with automatic rollback
    dep_raw = dep_path.read_text(encoding="utf-8")
    dep_doc = yaml.safe_load(dep_raw)
    jobs = dep_doc["jobs"]
    expected_jobs = (
        "security-gate",
        "build",
        "deploy-staging",
        "smoke-test-staging",
        "deploy-production",
        "validate-production",
        "rollback-production",
    )
    for job_name in expected_jobs:
        assert job_name in jobs, f"Missing job {job_name!r} in deploy-hardened.yml"

    assert jobs["security-gate"]["uses"] == "./.github/workflows/security-scan.yml"
    assert jobs["build"]["needs"] == ["security-gate"]
    assert jobs["deploy-staging"]["needs"] == ["build"]
    assert jobs["deploy-staging"]["environment"]["name"] == "staging"
    assert jobs["smoke-test-staging"]["needs"] == ["deploy-staging"]
    assert jobs["deploy-production"]["needs"] == ["smoke-test-staging"]
    assert jobs["deploy-production"]["environment"]["name"] == "production"
    assert jobs["validate-production"]["needs"] == ["deploy-production"]
    assert "failure" in str(jobs["rollback-production"]["if"])

    for token in (
        "scripts/deployment/check-freeze.sh",
        "scripts/deployment/smoke-tests.sh",
        "scripts/deployment/deployment-validation.sh",
        "scripts/deployment/auto-rollback.sh",
        "./deploy/helm/product-value-system",
        "issues.create",
    ):
        assert token in dep_raw, f"deploy-hardened.yml missing reference to {token!r}"

    # 3. smoke-tests.yml supports workflow_call & workflow_dispatch
    smoke_doc = yaml.safe_load(smoke_path.read_text(encoding="utf-8"))
    smoke_on = smoke_doc.get("on") or smoke_doc.get(True)
    assert "workflow_call" in smoke_on
    assert "workflow_dispatch" in smoke_on
    assert "smoke-tests" in smoke_doc["jobs"]


@pytest.mark.unit
def test_branch_protection_and_codeowners_hardened() -> None:
    """.github/branch-protection.json & CODEOWNERS require 2 approvals, CI/security & no force."""
    assert BRANCH_PROTECTION_PATH.exists()
    bp = json.loads(BRANCH_PROTECTION_PATH.read_text(encoding="utf-8"))

    assert bp["required_status_checks"]["strict"] is True
    contexts = " ".join(bp["required_status_checks"]["contexts"])
    for expected in ("Trivy", "Snyk", "CodeQL SAST", "Lint", "Test Suite"):
        assert expected in contexts, f"Missing required check context matching {expected!r}"

    assert bp["enforce_admins"] is True
    reviews = bp["required_pull_request_reviews"]
    assert reviews["required_approving_review_count"] == 2
    assert reviews["require_code_owner_reviews"] is True
    assert reviews["dismiss_stale_reviews"] is True
    assert bp["allow_force_pushes"] is False
    assert bp["allow_deletions"] is False
    assert bp["required_conversation_resolution"] is True

    assert CODEOWNERS_PATH.exists()
    codeowners = CODEOWNERS_PATH.read_text(encoding="utf-8")
    for guarded_path in ("/deploy/", "/.github/", "/src/product_value_system/security/"):
        assert guarded_path in codeowners


@pytest.mark.unit
def test_deployment_scripts_syntax_dry_run_freeze_calendar_and_security() -> None:
    """All 4 deployment scripts pass bash -n, --dry-run, live freeze checks, and secret scans."""
    bash_bin = _find_bash()
    detector = HybridSecretDetector()
    scripts = (
        "smoke-tests.sh",
        "deployment-validation.sh",
        "auto-rollback.sh",
        "check-freeze.sh",
    )

    for script_name in scripts:
        path = DEPLOY_SCRIPTS_DIR / script_name
        assert path.exists(), f"Missing {path}"
        content = path.read_text(encoding="utf-8")
        assert content.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in content
        assert detector.detect(content) == [], f"Secret finding in {script_name}"

        if bash_bin is not None:
            syntax = subprocess.run(
                [bash_bin, "-n", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            assert syntax.returncode == 0, f"bash -n failed for {script_name}: {syntax.stderr}"

            dry = subprocess.run(
                [bash_bin, str(path), "--dry-run"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert dry.returncode == 0, f"--dry-run failed for {script_name}: {dry.stderr}"
            assert "[DRY-RUN]" in dry.stdout

    if bash_bin is not None:
        freeze_script = str(DEPLOY_SCRIPTS_DIR / "check-freeze.sh")

        # Regular Tuesday -> allowed (exit 0)
        ok_day = subprocess.run(
            [bash_bin, freeze_script, "--date", "2026-11-10"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert ok_day.returncode == 0, ok_day.stderr
        assert "OK:" in ok_day.stdout

        # Christmas blackout -> blocked (exit 1)
        holiday = subprocess.run(
            [bash_bin, freeze_script, "--date", "2026-12-25"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert holiday.returncode == 1
        assert "BLOCKED:" in holiday.stderr

        # Weekend blackout -> blocked (exit 1)
        weekend = subprocess.run(
            [bash_bin, freeze_script, "--date", "2026-11-14"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert weekend.returncode == 1

        # Emergency override without reason -> rejected (exit 2)
        bad_override = subprocess.run(
            [bash_bin, freeze_script, "--date", "2026-12-25", "--emergency-override"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert bad_override.returncode == 2

        # Audited emergency override with reason -> allowed (exit 0)
        good_override = subprocess.run(
            [
                bash_bin,
                freeze_script,
                "--date",
                "2026-12-25",
                "--emergency-override",
                "--reason",
                "INC-20261225-0100: P0 auth hotfix",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert good_override.returncode == 0, good_override.stderr
        assert "P0 emergency override" in good_override.stdout


@pytest.mark.unit
def test_smoke_tests_script_live_against_ephemeral_fastapi_server(tmp_path: Path) -> None:
    """scripts/deployment/smoke-tests.sh passes 6/6 on healthy PVS app and fails on degraded DB."""
    bash_bin = _find_bash()
    if bash_bin is None:
        pytest.skip("bash binary unavailable")

    smoke_script = str(DEPLOY_SCRIPTS_DIR / "smoke-tests.sh")

    # 1. Healthy ephemeral PVS FastAPI instance -> 6/6 PASS
    healthy_app = create_app(
        metrics=PVSMetrics(register_global_prometheus=False),
        metrics_api_key="",
        enable_tracing=False,
    )
    server, thread, base_url = _start_ephemeral_server(healthy_app)
    try:
        out_dir = tmp_path / "healthy"
        res = subprocess.run(
            [bash_bin, smoke_script, "--target-url", base_url, "--results-dir", str(out_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"stdout={res.stdout}\nstderr={res.stderr}"
        report = json.loads((out_dir / "smoke-tests.json").read_text(encoding="utf-8"))
        assert report["total"] == 6
        assert report["passed"] == 6
        assert report["failed"] == 0
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    # 2. Degraded DB instance -> readiness_and_database FAILS (exit 1)
    degraded_app = create_app(
        metrics=PVSMetrics(register_global_prometheus=False),
        metrics_api_key="",
        db_check=lambda: False,
        enable_tracing=False,
    )
    server2, thread2, base_url2 = _start_ephemeral_server(degraded_app)
    try:
        out_dir2 = tmp_path / "degraded"
        res2 = subprocess.run(
            [bash_bin, smoke_script, "--target-url", base_url2, "--results-dir", str(out_dir2)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res2.returncode == 1
        report2 = json.loads((out_dir2 / "smoke-tests.json").read_text(encoding="utf-8"))
        assert report2["failed"] >= 1
        by_name = {item["test"]: item["status"] for item in report2["results"]}
        assert by_name["readiness_and_database"] == "fail"
    finally:
        server2.should_exit = True
        thread2.join(timeout=5)


@pytest.mark.unit
def test_deployment_metrics_dashboard_and_docs_complete() -> None:
    """Deployment metrics Grafana dashboard, process guide & freeze calendar are complete."""
    assert DEPLOY_DASHBOARD_PATH.exists()
    dash = json.loads(DEPLOY_DASHBOARD_PATH.read_text(encoding="utf-8"))
    assert dash["uid"] == "pvs-deployment-metrics"
    assert dash["schemaVersion"] >= 36
    assert len(dash["panels"]) == 6
    for panel in dash["panels"]:
        assert panel["targets"]
        for target in panel["targets"]:
            _assert_balanced_promql(target["expr"], panel["title"])

    detector = HybridSecretDetector()
    for doc_path in (DEPLOY_PROCESS_DOC, FREEZE_CALENDAR_DOC):
        assert doc_path.exists(), f"Missing {doc_path}"
        text = doc_path.read_text(encoding="utf-8")
        assert not PLACEHOLDER_RE.search(text), f"Placeholder marker in {doc_path}"
        assert detector.detect(text) == [], f"Secret finding in {doc_path.name}"

    proc_text = DEPLOY_PROCESS_DOC.read_text(encoding="utf-8")
    assert "```mermaid" in proc_text
    for token in ("Trivy", "Snyk", "CodeQL", "staging", "production", "auto-rollback.sh"):
        assert token in proc_text

    freeze_text = FREEZE_CALENDAR_DOC.read_text(encoding="utf-8")
    for token in ("2025", "2026", "2027", "check-freeze.sh", "Emergency P0 Hotfix"):
        assert token in freeze_text
