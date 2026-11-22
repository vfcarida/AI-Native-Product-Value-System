"""Unit test validating the reproducible empirical benchmark harness (Round 10)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_benchmark_module():
    script_path = REPO_ROOT / "scripts" / "run_benchmarks.py"
    spec = importlib.util.spec_from_file_location("run_benchmarks", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_reproducible_benchmark_suite_outputs_valid_metrics() -> None:
    mod = _load_benchmark_module()
    report = await mod.run_all_benchmarks(quick=True)

    assert report["metadata"]["external_api_cost_usd"] == 0.0
    b = report["benchmarks"]
    assert b["policy_engine"]["auth_ops_per_sec"] > 100.0
    assert b["policy_engine"]["auth_eval"]["p50_ms"] <= b["policy_engine"]["auth_eval"]["p99_ms"]
    assert b["secret_detector"]["false_positives_on_clean_doc"] == 0
    assert b["secret_detector"]["synthetic_probes_detected"] == "2/2"
    assert b["model_routing"]["cost_reduction_vs_flagship_pct"] > 30.0
    assert b["parallel_orchestrator"]["empirical_speedup_x"] > 1.3
    assert b["batch_evaluation"]["openai_savings_pct"] == 50.0
    assert b["brownfield_modularity"]["structural_modularity_q"] > 0.5

    md = mod.render_markdown_report(report)
    assert "Empirical Benchmarks" in md
    assert "Cedar Policy Engine" in md
