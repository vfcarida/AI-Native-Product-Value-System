"""
Validation tests for PROD-009 k6 Load Testing Suite, Regression Analyzer,
Capacity Planning Model, CI Workflow, Grafana Dashboard, and In-Process SLO Smoke Load.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from product_value_system.api.health import create_app
from product_value_system.security.secret_detector import HybridSecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]
LOAD_DIR = REPO_ROOT / "tests" / "load"
SCRIPTS_DIR = REPO_ROOT / "scripts"
FIXTURES_PATH = LOAD_DIR / "fixtures" / "test-data.json"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "performance-regression.yml"
DASHBOARD_PATH = REPO_ROOT / "grafana" / "dashboards" / "load-testing-dashboard.json"
GUIDE_PATH = REPO_ROOT / "docs" / "operations" / "load-testing-guide.md"


def _load_script_module(script_name: str, module_name: str) -> Any:
    script_path = SCRIPTS_DIR / script_name
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _sample_summary(
    *,
    p50: float = 420.0,
    p95: float = 780.0,
    p99: float = 950.0,
    err_rate: float = 0.004,
    rps: float = 12.0,
    count: int = 1000,
    tokens: int = 1_200_000,
) -> dict[str, Any]:
    return {
        "metrics": {
            "http_req_duration": {"values": {"p(50)": p50, "p(95)": p95, "p(99)": p99}},
            "errors": {"values": {"rate": err_rate}},
            "http_reqs": {"values": {"count": count, "rate": rps}},
            "tokens_used": {"values": {"count": tokens}},
        }
    }


# ---------------------------------------------------------------------------
# 1. k6 Script Syntax, Scenarios & SLO Thresholds
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_k6_scripts_exist_and_parse_valid_javascript() -> None:
    """All 6 k6 scripts exist, parse cleanly via k6 inspect or Node.js ESM parser."""
    expected = {
        "k6-common.js",
        "k6-greenfield.js",
        "k6-brownfield.js",
        "k6-stress-test.js",
        "k6-soak-test.js",
        "k6-spike-test.js",
    }
    actual = {p.name for p in LOAD_DIR.glob("k6-*.js")}
    assert expected.issubset(actual)

    k6_bin = shutil.which("k6")
    node_bin = shutil.which("node")

    for name in sorted(expected):
        script_path = LOAD_DIR / name
        source = script_path.read_text(encoding="utf-8")
        assert len(source) > 100

        if k6_bin and name != "k6-common.js":
            proc = subprocess.run(
                [k6_bin, "inspect", str(script_path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert proc.returncode == 0, f"k6 inspect failed on {name}: {proc.stderr}"
        elif node_bin:
            # Strip k6 virtual imports & export keywords to syntax-verify via `new Function` / VM
            # Using node --check on a temporary ESM-stripped copy validates full JS grammar
            check_js = (
                "import vm from 'node:vm';\n"
                "import fs from 'node:fs';\n"
                f"const raw = fs.readFileSync({json.dumps(str(script_path))}, 'utf8');\n"
                "const stripped = raw\n"
                "  .replace(/^\\s*import\\s+[\\s\\S]*?from\\s+['\"][^'\"]+['\"];?\\s*$/gm, '')\n"
                "  .replace(/^\\s*export\\s+default\\s+/gm, 'const __default = ')\n"
                "  .replace(/^\\s*export\\s+/gm, '');\n"
                "new vm.Script(stripped, { filename: 'check.js' });\n"
            )
            proc = subprocess.run(
                [node_bin, "--input-type=module", "-e", check_js],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            assert proc.returncode == 0, f"JS syntax error in {name}: {proc.stderr}"


@pytest.mark.unit
def test_k6_scripts_define_required_slo_thresholds_and_executors() -> None:
    """Each workload declares its required k6 executor and SLO threshold gates."""
    gf = (LOAD_DIR / "k6-greenfield.js").read_text(encoding="utf-8")
    assert "thresholds" in gf and "p(99)<5000" in gf and "rate<0.01" in gf

    bf = (LOAD_DIR / "k6-brownfield.js").read_text(encoding="utf-8")
    assert "thresholds" in bf and "p(99)<10000" in bf and "rate<0.01" in bf

    stress = (LOAD_DIR / "k6-stress-test.js").read_text(encoding="utf-8")
    assert "ramping-arrival-rate" in stress and "target: 10000" in stress

    soak = (LOAD_DIR / "k6-soak-test.js").read_text(encoding="utf-8")
    assert "constant-vus" in soak and "24h" in soak and "p(99)<1000" in soak

    spike = (LOAD_DIR / "k6-spike-test.js").read_text(encoding="utf-8")
    assert "duration: '10s', target: 100" in spike and "r.status === 503" in spike


@pytest.mark.unit
def test_load_test_fixtures_realistic_and_free_of_secrets_or_pii() -> None:
    """tests/load/fixtures/test-data.json provides >=12 projects with zero PII or secrets."""
    raw = FIXTURES_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    gf = data["greenfield_projects"]
    bf = data["brownfield_projects"]
    assert len(gf) >= 6
    assert len(bf) >= 6
    assert len(gf) + len(bf) >= 12
    assert data["metadata"]["seed"] == 42

    # Ensure zero PII emails and zero secrets in fixtures or k6 scripts
    assert re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", raw) is None
    detector = HybridSecretDetector()
    for target in [FIXTURES_PATH, *LOAD_DIR.glob("k6-*.js")]:
        findings = detector.detect(target.read_text(encoding="utf-8"))
        assert findings == [], f"Unexpected secret finding in {target}: {findings}"


# ---------------------------------------------------------------------------
# 2. Regression Analysis Script (`scripts/analyze-k6-results.py`)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_analyze_k6_results_passes_within_10pct_threshold(tmp_path: Path) -> None:
    """Minor +3.7% latency fluctuation passes cleanly (exit code 0) and writes markdown/json."""
    mod = _load_script_module("analyze-k6-results.py", "analyze_k6_results")
    base_p = tmp_path / "baseline.json"
    curr_p = tmp_path / "current.json"
    md_out = tmp_path / "report.md"
    json_out = tmp_path / "report.json"

    base_p.write_text(json.dumps(_sample_summary(p99=950.0, rps=12.0)), encoding="utf-8")
    curr_p.write_text(json.dumps(_sample_summary(p99=985.0, rps=11.8)), encoding="utf-8")

    rc = mod.main(
        [
            str(curr_p),
            str(base_p),
            "--slo-p99-max-ms",
            "1000",
            "--markdown-out",
            str(md_out),
            "--json-out",
            str(json_out),
        ]
    )
    assert rc == 0
    assert "PASS" in md_out.read_text(encoding="utf-8")
    parsed = json.loads(json_out.read_text(encoding="utf-8"))
    assert parsed["passed"] is True
    assert parsed["violations"] == []


@pytest.mark.unit
def test_analyze_k6_results_fails_on_p99_throughput_or_error_regression(tmp_path: Path) -> None:
    """Regressions >10% latency, >50% error rate, or >10% throughput drop fail CI (exit 1)."""
    mod = _load_script_module("analyze-k6-results.py", "analyze_k6_results")
    base_p = tmp_path / "baseline.json"
    curr_p = tmp_path / "current.json"

    base_p.write_text(
        json.dumps(_sample_summary(p99=900.0, err_rate=0.005, rps=100.0)),
        encoding="utf-8",
    )
    curr_p.write_text(
        json.dumps(_sample_summary(p99=1150.0, err_rate=0.02, rps=80.0)),
        encoding="utf-8",
    )

    rc = mod.main([str(curr_p), str(base_p), "--slo-p99-max-ms", "1000"])
    assert rc == 1

    current = mod.parse_k6_summary(curr_p)
    baseline = mod.parse_k6_summary(base_p)
    changes = mod.compare_baselines(current, baseline)
    violations = mod.detect_regression(changes, current=current, slo_p99_max_ms=1000.0)
    assert len(violations) == 4


# ---------------------------------------------------------------------------
# 3. Capacity Planning Model (`scripts/capacity-planning.py`)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_capacity_planning_model_projects_pods_nodes_and_monthly_cost(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Capacity planner computes ceiling pod/node counts and unit economics from k6 summary."""
    mod = _load_script_module("capacity-planning.py", "capacity_planning")
    summary = tmp_path / "stress.json"
    summary.write_text(
        json.dumps(_sample_summary(rps=2100.0, count=50000, tokens=50_000_000)),
        encoding="utf-8",
    )

    rc = mod.main([str(summary), "--targets", "1000", "10000", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["model"]["max_rps"] == pytest.approx(2100.0)
    assert payload["model"]["avg_tokens_per_request"] == pytest.approx(1000.0)

    proj_1k, proj_10k = payload["projections"]
    assert proj_1k["pods_needed"] == 20
    assert proj_1k["nodes_needed"] == 2
    assert proj_1k["monthly_cost_usd"] > 0
    assert proj_10k["pods_needed"] == 200
    assert proj_10k["nodes_needed"] == 20
    assert proj_10k["cost_per_user_per_month"] > 0


# ---------------------------------------------------------------------------
# 4. CI Workflow, Grafana Dashboard & In-Process Concurrent SLO Smoke Load
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ci_workflow_dashboard_and_guide_valid() -> None:
    """Performance regression workflow, Grafana dashboard PromQL, and guide are complete."""
    from tests.monitoring.test_slo_definitions import _assert_balanced_promql

    wf = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert "load-test" in wf["jobs"]
    assert wf["jobs"]["load-test"]["timeout-minutes"] == 60
    wf_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "analyze-k6-results.py" in wf_text
    assert "[skip-perf-test]" in wf_text

    dash = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
    assert dash["uid"] == "pvs-load-testing"
    assert len(dash["panels"]) == 6
    for panel in dash["panels"]:
        for target in panel["targets"]:
            _assert_balanced_promql(target["expr"], panel["title"])

    guide = GUIDE_PATH.read_text(encoding="utf-8")
    for token in ("Greenfield", "Brownfield", "Stress", "Soak", "Spike", "capacity-planning.py"):
        assert token in guide


@pytest.mark.asyncio
async def test_in_process_concurrent_load_smoke_meets_slo_under_1s_p99() -> None:
    """50 concurrent HTTP requests against create_app() meet p99 < 1000ms and 0% error rate."""
    app = create_app(metrics_api_key="")
    latencies_ms: list[float] = []
    errors = 0

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:

        async def _hit() -> None:
            nonlocal errors
            t0 = time.perf_counter()
            resp = await client.get("/health")
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            if resp.status_code != 200:
                errors += 1

        await asyncio.gather(*[_hit() for _ in range(50)])

    latencies_ms.sort()
    p99_ms = latencies_ms[int(len(latencies_ms) * 0.99) - 1]
    assert errors == 0
    assert p99_ms < 1000.0
