"""
End-to-end integration tests for EVO-017 Drift Detection, Pre-Commit Hook & CLI.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from product_value_system.cli.main import main as cli_main
from product_value_system.drift import (
    DriftReporter,
    DriftSeverity,
    compare_traces_with_spec,
    detect_drift,
    scan_drift,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_detect_drift_in_brownfield_payment_agent(tmp_path: Path) -> None:
    """Detect added require_biometric parameter in brownfield payment agent and generate report."""
    code_dir = tmp_path / "src"
    spec_dir = tmp_path / "docs"
    code_dir.mkdir()
    spec_dir.mkdir()

    (code_dir / "payment.py").write_text(
        '''
async def initiate_transfer(
    amount: float,
    destination: str,
    require_biometric: bool = False,
) -> dict[str, str]:
    """Initiate payment transfer."""
    return {"status": "pending"}
''',
        encoding="utf-8",
    )
    (spec_dir / "api.md").write_text(
        '''# Payment API

```python
async def initiate_transfer(amount: float, destination: str) -> dict[str, str]:
    """Initiate payment transfer."""
```
''',
        encoding="utf-8",
    )

    findings = detect_drift(code_paths=[code_dir], spec_paths=[spec_dir])
    assert len(findings) == 1
    assert "require_biometric" in findings[0].description
    assert findings[0].severity == DriftSeverity.MODERATE

    report_path = tmp_path / "drift_report.md"
    DriftReporter().generate_markdown_report(findings, report_path)
    assert report_path.exists()
    assert "require_biometric" in report_path.read_text(encoding="utf-8")


@pytest.mark.integration
def test_zero_false_positives_when_spec_updated_with_code(tmp_path: Path) -> None:
    """Intentional code change accompanied by spec update yields zero drift findings."""
    code_dir = tmp_path / "src"
    spec_dir = tmp_path / "docs"
    code_dir.mkdir()
    spec_dir.mkdir()

    (code_dir / "payment.py").write_text(
        '''
async def initiate_transfer(
    amount: float,
    destination: str,
    require_biometric: bool = False,
) -> dict[str, str]:
    """Initiate payment transfer."""
    return {"status": "pending"}
''',
        encoding="utf-8",
    )
    (spec_dir / "api.md").write_text(
        '''# Payment API

```python
async def initiate_transfer(
    amount: float,
    destination: str,
    require_biometric: bool = False,
) -> dict[str, str]:
    """Initiate payment transfer."""
```
''',
        encoding="utf-8",
    )
    assert detect_drift(code_paths=[code_dir], spec_paths=[spec_dir]) == []


@pytest.mark.integration
def test_brownfield_codebase_without_specs_succeeds_cleanly(tmp_path: Path) -> None:
    """Brownfield code directory with no specs produces zero crashes and zero false positives."""
    code_dir = tmp_path / "legacy_src"
    empty_spec_dir = tmp_path / "empty_docs"
    code_dir.mkdir()
    empty_spec_dir.mkdir()

    (code_dir / "legacy.py").write_text("def legacy_fn(x: int) -> int:\n    return x + 1\n")

    scan = scan_drift(code_paths=[code_dir], spec_paths=[empty_spec_dir])
    assert scan.code_functions_count == 1
    assert scan.spec_functions_count == 0
    assert scan.findings == []


@pytest.mark.integration
def test_fastapi_and_openapi_endpoint_drift_e2e(tmp_path: Path) -> None:
    """Detect removed OpenAPI endpoint and mismatched request schema in FastAPI routes."""
    code_dir = tmp_path / "src"
    spec_dir = tmp_path / "docs"
    code_dir.mkdir()
    spec_dir.mkdir()

    (code_dir / "routes.py").write_text(
        '''
@app.post("/transfers")
async def create_transfer(amount: float, biometric_token: str) -> dict[str, str]:
    return {"status": "ok"}
''',
        encoding="utf-8",
    )
    (spec_dir / "openapi.yaml").write_text(
        """openapi: "3.0.3"
paths:
  /transfers:
    post:
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                amount: {type: number}
                destination: {type: string}
      responses:
        "200":
          description: OK
  /transfers/batch:
    post:
      responses:
        "200":
          description: Batch created
""",
        encoding="utf-8",
    )

    findings = detect_drift(code_paths=[code_dir], spec_paths=[spec_dir])
    descriptions = [f.description for f in findings]
    assert any("biometric_token" in d for d in descriptions)
    assert any("destination" in d for d in descriptions)
    assert any("/transfers/batch" in d for d in descriptions)


@pytest.mark.integration
def test_opentelemetry_traces_json_and_jsonl_behavioral_drift_e2e(tmp_path: Path) -> None:
    """Compare OpenTelemetry trace JSON/JSONL files against Markdown performance SLA specs."""
    spec_file = tmp_path / "performance.md"
    spec_file.write_text(
        """## initiate_transfer
`initiate_transfer` should complete in 2-5 seconds under normal conditions.
""",
        encoding="utf-8",
    )
    traces_json = tmp_path / "traces.json"
    traces_json.write_text(
        json.dumps(
            {
                "spans": [
                    {"operation": "initiate_transfer", "duration_ms": 8234},
                    {"operation": "initiate_transfer", "duration_ms": 7891},
                    {"operation": "initiate_transfer", "duration_ms": 8567},
                ]
            }
        ),
        encoding="utf-8",
    )
    findings = compare_traces_with_spec(traces_json, spec_file)
    assert len(findings) == 1
    assert findings[0].category == "behavior"
    assert findings[0].severity == DriftSeverity.MAJOR
    assert "initiate_transfer" in findings[0].description

    # Also test JSONL trace export format
    traces_jsonl = tmp_path / "traces.jsonl"
    traces_jsonl.write_text(
        '\n'.join(
            [
                json.dumps({"operation": "initiate_transfer", "duration_ms": 11000}),
                json.dumps({"operation": "initiate_transfer", "duration_ms": 12000}),
            ]
        ),
        encoding="utf-8",
    )
    jsonl_findings = compare_traces_with_spec(traces_jsonl, spec_file)
    assert len(jsonl_findings) == 1
    assert jsonl_findings[0].severity == DriftSeverity.CRITICAL  # >2x SLA


@pytest.mark.integration
def test_alert_threshold_and_brownfield_baseline_workflow(tmp_path: Path) -> None:
    """Alert fires when drift >= threshold, and baseline suppresses pre-existing findings."""
    code_dir = tmp_path / "src"
    spec_dir = tmp_path / "docs"
    code_dir.mkdir()
    spec_dir.mkdir()

    (code_dir / "pay.py").write_text(
        "def pay(a: int, b: str, c: float) -> bool:\n    return True\n",
        encoding="utf-8",
    )
    (spec_dir / "pay.md").write_text(
        "```python\ndef pay(a: int) -> bool:\n    '''Pay.'''\n```\n",
        encoding="utf-8",
    )

    alerts: list[str] = []
    initial = detect_drift(
        code_paths=[code_dir],
        spec_paths=[spec_dir],
        alert_threshold=2,
        alert_callback=lambda msg: alerts.append(msg),
    )
    assert len(initial) == 2
    assert len(alerts) == 1

    baseline_path = tmp_path / "baseline.json"
    DriftReporter().generate_report(initial, baseline_path)
    after_baseline = detect_drift(
        code_paths=[code_dir],
        spec_paths=[spec_dir],
        baseline_path=baseline_path,
    )
    assert after_baseline == []


@pytest.mark.integration
def test_pre_commit_drift_hook_blocks_critical_and_passes_clean(tmp_path: Path) -> None:
    """Pre-commit hook script exits 1 on critical drift and 0 when aligned."""
    code_dir = tmp_path / "src"
    spec_dir = tmp_path / "docs"
    code_dir.mkdir()
    spec_dir.mkdir()

    (code_dir / "svc.py").write_text("def run_job(x: int) -> bool:\n    return True\n")
    (spec_dir / "svc.md").write_text(
        "```python\ndef run_job(x: int, removed_param: str) -> bool:\n    '''Run.'''\n```\n"
    )

    hook_script = REPO_ROOT / "scripts" / "pre-commit-drift-check.py"
    blocked = subprocess.run(
        [
            sys.executable,
            str(hook_script),
            "--code",
            str(code_dir),
            "--spec",
            str(spec_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert blocked.returncode == 1
    assert "removed_param" in blocked.stdout

    # Fix spec -> pre-commit hook passes cleanly
    (spec_dir / "svc.md").write_text(
        "```python\ndef run_job(x: int) -> bool:\n    '''Run.'''\n```\n"
    )
    passed = subprocess.run(
        [
            sys.executable,
            str(hook_script),
            "--code",
            str(code_dir),
            "--spec",
            str(spec_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert passed.returncode == 0


@pytest.mark.integration
def test_cli_drift_check_report_and_install_hook(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI `pvs drift` check, report, save-baseline, baseline, and install-hook work e2e."""
    code_dir = tmp_path / "src"
    spec_dir = tmp_path / "docs"
    code_dir.mkdir()
    spec_dir.mkdir()

    (code_dir / "transfer.py").write_text(
        "def transfer(amount: float, destination: str, biometric: bool = False) -> bool:\n"
        "    return True\n",
        encoding="utf-8",
    )
    (spec_dir / "transfer.md").write_text(
        "```python\n"
        "def transfer(amount: float, destination: str) -> bool:\n"
        "    '''Transfer.'''\n"
        "```\n",
        encoding="utf-8",
    )

    md_report = tmp_path / "weekly_drift.md"
    baseline_file = tmp_path / "initial_drift.json"

    # 1. `pvs drift check` detects drift and returns exit code 1 + writes baseline
    rc_check = cli_main(
        [
            "drift",
            "check",
            "--code",
            str(code_dir),
            "--spec",
            str(spec_dir),
            "--save-baseline",
            str(baseline_file),
        ]
    )
    assert rc_check == 1
    out_check = capsys.readouterr().out
    assert "biometric" in out_check
    assert baseline_file.exists()

    # 2. Re-running with `--baseline` passes with exit code 0
    rc_baselined = cli_main(
        [
            "drift",
            "check",
            "--code",
            str(code_dir),
            "--spec",
            str(spec_dir),
            "--baseline",
            str(baseline_file),
        ]
    )
    assert rc_baselined == 0

    # 3. `pvs drift report --since "7 days ago"` writes Markdown report
    rc_report = cli_main(
        [
            "drift",
            "report",
            "--code",
            str(code_dir),
            "--spec",
            str(spec_dir),
            "--since",
            "7 days ago",
            "--output",
            str(md_report),
        ]
    )
    assert rc_report == 0
    assert md_report.exists()
    assert "biometric" in md_report.read_text(encoding="utf-8")

    # 4. `pvs drift install-hook` installs hook into isolated temp hooks dir
    hooks_dir = tmp_path / "git_hooks"
    rc_hook = cli_main(["drift", "install-hook", "--hooks-dir", str(hooks_dir)])
    assert rc_hook == 0
    hook_file = hooks_dir / "pre-commit"
    assert hook_file.exists()
    assert "pre-commit-drift-check.py" in hook_file.read_text(encoding="utf-8")
