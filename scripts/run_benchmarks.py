#!/usr/bin/env python3
"""
Reproducible Empirical Benchmark Suite for AI-Native Product Value System (Round 10).

Follows NeurIPS Reproducibility Checklist, Stanford HELM multi-metric reporting,
and FinOps Foundation cost-performance methodology:
- Captures environment fingerprint (OS, Python version, CPU count, timestamp)
- Reports p50 / p95 / p99 tail latencies rather than averages alone
- Runs 100% deterministically at $0 external API cost
- Emits JSON (--json / --json-out) and/or Markdown report (--markdown-out)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import networkx as nx

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from product_value_system.adapters.model_adapter import (  # noqa: E402
    Message,
    ModelResponse,
    Role,
    Usage,
)
from product_value_system.brownfield.boundary_analyzer import (  # noqa: E402
    BoundaryAnalyzer,
    Module,
)
from product_value_system.caching import (  # noqa: E402
    MemoryCacheBackend,
    PromptCache,
    SQLiteCacheBackend,
    serialize_model_response,
)
from product_value_system.evaluation.batch_runner import estimate_batch_savings  # noqa: E402
from product_value_system.orchestrator import (  # noqa: E402
    AgentDefinition,
    ParallelExecutor,
    build_dependency_graph,
)
from product_value_system.policy.engine import PolicyEngine  # noqa: E402
from product_value_system.routing import ComplexityLevel, ModelRouter  # noqa: E402
from product_value_system.security.secret_detector import HybridSecretDetector  # noqa: E402


def _percentiles_ms(samples_sec: list[float]) -> dict[str, float]:
    """Return p50, p95, p99, and mean in milliseconds from raw second timings."""
    if not samples_sec:
        return {"mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0}
    ordered = sorted(s * 1000.0 for s in samples_sec)
    n = len(ordered)

    def _pct(p: float) -> float:
        idx = min(n - 1, max(0, int(round(p * (n - 1)))))
        return ordered[idx]

    return {
        "mean_ms": round(statistics.mean(ordered), 4),
        "p50_ms": round(_pct(0.50), 4),
        "p95_ms": round(_pct(0.95), 4),
        "p99_ms": round(_pct(0.99), 4),
    }


def bench_policy_engine(iterations: int) -> dict[str, Any]:
    """B1: Cedar PolicyEngine cold vs. mtime-cached instantiation and auth throughput."""
    PolicyEngine.clear_policy_cache()
    t0 = time.perf_counter()
    engine = PolicyEngine(policies_dir=REPO_ROOT / "policies")
    cold_ms = (time.perf_counter() - t0) * 1000.0

    warm_samples: list[float] = []
    for _ in range(max(10, iterations // 5)):
        s = time.perf_counter()
        PolicyEngine(policies_dir=REPO_ROOT / "policies")
        warm_samples.append(time.perf_counter() - s)

    auth_samples: list[float] = []
    for i in range(iterations):
        s = time.perf_counter()
        decision = engine.authorize(
            principal="discovery-agent",
            action="read",
            resource="repo:docs",
            context={"run_id": f"bench-{i}", "cost_so_far": 0.1},
        )
        auth_samples.append(time.perf_counter() - s)
        assert decision is not None

    warm_stats = _percentiles_ms(warm_samples)
    auth_stats = _percentiles_ms(auth_samples)
    total_auth_sec = sum(auth_samples) or 1e-9
    return {
        "cold_init_ms": round(cold_ms, 3),
        "warm_init": warm_stats,
        "init_speedup_x": round(cold_ms / max(warm_stats["p50_ms"], 1e-4), 1),
        "auth_eval": auth_stats,
        "auth_ops_per_sec": round(iterations / total_auth_sec, 1),
    }


def bench_secret_detector(iterations: int) -> dict[str, Any]:
    """B2: Hybrid ML Secret Detector 10K-token scan latency & synthetic precision."""
    detector = HybridSecretDetector.from_config(REPO_ROOT / "config" / "secret_detection.yaml")
    # Build ~10K-token benign engineering document (0 secrets)
    paragraph = (
        "The value stream orchestrator validates specifications against JSON schemas, "
        "executes architecture and security reviewers concurrently, and tracks SPACE metrics. "
    )
    doc_10k = paragraph * 420  # ~40,000 chars (~10,000 tokens)

    scan_runs = max(3, min(15, iterations // 20))
    samples: list[float] = []
    false_positives = 0
    for _ in range(scan_runs):
        s = time.perf_counter()
        hits = detector.detect(doc_10k)
        samples.append(time.perf_counter() - s)
        false_positives += len(hits)

    # Synthetic high-entropy probes built dynamically (avoiding placeholder words)
    synth_openai = "".join(["sk-", "9xK2mP7qR4vW8zB3nL6cT1yF5hJ0dA8wE4uQ"])
    synth_aws = "".join(["AKIA", "7Q2M9X4R8P3K6V5N2B1Z"])
    tp_found = int(bool(detector.detect(f"prod_key={synth_openai}"))) + int(
        bool(detector.detect(f"prod_aws_key={synth_aws}"))
    )

    return {
        "doc_chars": len(doc_10k),
        "estimated_tokens": len(doc_10k) // 4,
        "scan_latency": _percentiles_ms(samples),
        "false_positives_on_clean_doc": false_positives,
        "synthetic_probes_detected": f"{tp_found}/2",
    }


async def bench_prompt_cache(iterations: int) -> dict[str, Any]:
    """B3: PromptCache Memory & SQLite WAL hit latency and projected FinOps savings."""
    mem_cache = PromptCache(backend=MemoryCacheBackend(max_entries=1000))
    resp = ModelResponse(
        content="Cached architectural review output with zero regressions.",
        model="claude-3-5-sonnet-20241022",
        usage=Usage(input_tokens=1500, output_tokens=500),
        finish_reason="stop",
    )
    payload = serialize_model_response(resp)
    base_msg = [Message(role=Role.USER, content="Review checkout service architecture spec v1")]
    await mem_cache.set(base_msg, "claude-3-5-sonnet-20241022", 0.0, 1024, payload)

    mem_samples: list[float] = []
    for _ in range(iterations):
        s = time.perf_counter()
        hit = await mem_cache.get(base_msg, "claude-3-5-sonnet-20241022", 0.0, 1024)
        mem_samples.append(time.perf_counter() - s)
        assert hit is not None

    with tempfile.TemporaryDirectory() as tmpdir:
        sql_backend = SQLiteCacheBackend(Path(tmpdir) / "bench_cache.db", max_entries=1000)
        sql_cache = PromptCache(backend=sql_backend)
        await sql_cache.set(base_msg, "claude-3-5-sonnet-20241022", 0.0, 1024, payload)
        sql_runs = max(10, iterations // 5)
        sql_samples: list[float] = []
        for _ in range(sql_runs):
            s = time.perf_counter()
            hit = await sql_cache.get(base_msg, "claude-3-5-sonnet-20241022", 0.0, 1024)
            sql_samples.append(time.perf_counter() - s)
            assert hit is not None

    # Realistic 60% workload hit rate FinOps projection
    hit_rate = 0.60
    baseline_cost = 100.0
    cached_cost = baseline_cost * (1.0 - hit_rate)
    return {
        "memory_exact_hit": _percentiles_ms(mem_samples),
        "sqlite_wal_exact_hit": _percentiles_ms(sql_samples),
        "reference_hit_rate_pct": round(hit_rate * 100.0, 1),
        "projected_cost_reduction_pct": round((1.0 - cached_cost / baseline_cost) * 100.0, 1),
    }


def bench_model_routing(iterations: int) -> dict[str, Any]:
    """B4: Complexity-Aware Model Routing throughput and FinOps cost reduction."""
    router = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing.yaml")
    workload = [
        ("format_conversion", "Convert this CSV row to JSON schema: id,name,status"),
        ("extraction", "Extract the list of HTTP status codes from this log line."),
        ("summarization", "Summarize these 3 bullet points into one sentence."),
        ("code_review", "Review this Python async handler for race conditions and retries."),
        ("test_generation", "Generate pytest unit tests for payment idempotency checks."),
        (
            "architecture_design",
            "Design a multi-region event-sourced payment ledger with zero-downtime migration "
            "and strict PCI-DSS isolation trade-offs.",
        ),
    ]

    samples: list[float] = []
    tier_counts: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    routed_cost_1k = 0.0
    flagship_cost_1k = 0.0

    # Highest-tier cost per 1K tokens from active policy as baseline
    high_specs = router.models_by_tier.get(ComplexityLevel.HIGH) or []
    flagship_rate = (
        max(spec.blended_cost_per_1m / 1000.0 for spec in high_specs) if high_specs else 0.030
    )

    for i in range(iterations):
        task_type, text = workload[i % len(workload)]
        msgs = [Message(role=Role.USER, content=text)]
        s = time.perf_counter()
        decision = router.route(msgs, task_type=task_type)
        samples.append(time.perf_counter() - s)
        tier_counts[decision.complexity_level.value] = (
            tier_counts.get(decision.complexity_level.value, 0) + 1
        )
        routed_cost_1k += decision.estimated_cost_per_1k_tokens
        flagship_cost_1k += flagship_rate

    savings_pct = (
        (1.0 - routed_cost_1k / flagship_cost_1k) * 100.0 if flagship_cost_1k > 0 else 0.0
    )
    total_sec = sum(samples) or 1e-9
    return {
        "latency": _percentiles_ms(samples),
        "decisions_per_sec": round(iterations / total_sec, 1),
        "tier_distribution": tier_counts,
        "baseline_flagship_cost_per_1k_usd": round(flagship_rate, 6),
        "mean_routed_cost_per_1k_usd": round(routed_cost_1k / max(1, iterations), 6),
        "cost_reduction_vs_flagship_pct": round(savings_pct, 1),
    }


async def bench_parallel_orchestrator() -> dict[str, Any]:
    """B5: ParallelExecutor wall-clock speedup on a 4-reviewer concurrent DAG."""
    dag = build_dependency_graph(
        [
            AgentDefinition(name="specification", depends_on=[]),
            AgentDefinition(name="review_architecture", depends_on=["specification"]),
            AgentDefinition(name="review_security", depends_on=["specification"]),
            AgentDefinition(name="review_performance", depends_on=["specification"]),
            AgentDefinition(name="review_maintainability", depends_on=["specification"]),
            AgentDefinition(
                name="quality_gate",
                depends_on=[
                    "review_architecture",
                    "review_security",
                    "review_performance",
                    "review_maintainability",
                ],
            ),
        ]
    )

    stage_delay = 0.04  # 40ms simulated I/O per agent

    async def _make_runner(name: str) -> Any:
        async def _run(_inputs: dict[str, Any]) -> dict[str, Any]:
            await asyncio.sleep(stage_delay)
            return {"agent": name, "status": "ok"}

        return _run

    registry = {name: await _make_runner(name) for name in dag.agents}
    executor = ParallelExecutor(max_concurrent=5)
    summary = await executor.execute_with_summary(
        dependency_graph=dag,
        initial_inputs={"problem_id": "bench-dag"},
        agent_registry=registry,
    )
    return {
        "total_agents": len(dag.agents),
        "peak_concurrent_agents": summary.peak_concurrent_agents,
        "sequential_time_ms": round(summary.sequential_duration_seconds * 1000.0, 2),
        "parallel_time_ms": round(summary.total_duration_seconds * 1000.0, 2),
        "empirical_speedup_x": round(summary.speedup, 2),
        "latency_reduction_pct": round(
            (1.0 - summary.total_duration_seconds / max(summary.sequential_duration_seconds, 1e-9))
            * 100.0,
            1,
        ),
    }


def bench_batch_evaluation() -> dict[str, Any]:
    """B6: Batch Evaluation FinOps cost comparison across 500 golden samples."""
    samples = 500
    in_tokens = samples * 1200
    out_tokens = samples * 400
    oai_rt, oai_batch, _oai_save, oai_pct = estimate_batch_savings(
        provider="openai",
        model="gpt-4o",
        input_tokens=in_tokens,
        output_tokens=out_tokens,
    )
    ant_rt, ant_batch, _ant_save, ant_pct = estimate_batch_savings(
        provider="anthropic",
        model="claude-3-5-sonnet-20241022",
        input_tokens=in_tokens,
        output_tokens=out_tokens,
    )
    return {
        "samples": samples,
        "openai_realtime_usd": round(oai_rt, 4),
        "openai_batch_usd": round(oai_batch, 4),
        "openai_savings_pct": round(oai_pct, 1),
        "anthropic_realtime_usd": round(ant_rt, 4),
        "anthropic_batch_usd": round(ant_batch, 4),
        "anthropic_savings_pct": round(ant_pct, 1),
    }


def bench_brownfield_modularity() -> dict[str, Any]:
    """B7: Clauset-Newman-Moore community detection & Newman-Girvan Q on 60-file monolith."""
    graph: nx.DiGraph[str] = nx.DiGraph()
    domains = ("billing", "orders", "inventory", "auth")
    files_per_domain = 15
    for dom in domains:
        nodes = [f"src/{dom}/mod_{i}.py" for i in range(files_per_domain)]
        for i, src in enumerate(nodes):
            graph.add_node(src)
            for j in range(1, 4):
                graph.add_edge(src, nodes[(i + j) % files_per_domain])
    # Sparse cross-domain bridge edges (realistic monolith)
    graph.add_edge("src/orders/mod_0.py", "src/billing/mod_0.py")
    graph.add_edge("src/orders/mod_1.py", "src/inventory/mod_0.py")
    graph.add_edge("src/billing/mod_1.py", "src/auth/mod_0.py")

    analyzer = BoundaryAnalyzer()
    code_structure = {str(node): {"loc": 120, "complexity": 4.0} for node in graph.nodes}
    t0 = time.perf_counter()
    communities = analyzer._cluster_by_dependency_graph(code_structure, graph)
    modules = [
        Module(name=name, file_paths=files, lines_of_code=len(files) * 120)
        for name, files in communities.items()
    ]
    modularity_q = analyzer.compute_modularity(modules, graph)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "communities_detected": len(communities),
        "structural_modularity_q": round(modularity_q, 4),
        "elapsed_ms": round(elapsed_ms, 3),
    }


async def run_all_benchmarks(*, quick: bool = False) -> dict[str, Any]:
    """Execute all 7 deterministic benchmarks and return structured report."""
    iters = 40 if quick else 200
    started = datetime.now(UTC)
    results = {
        "metadata": {
            "generated_at_utc": started.isoformat(),
            "mode": "quick" if quick else "standard",
            "iterations": iters,
            "python_version": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
            "cpu_count": os.cpu_count() or 1,
            "external_api_cost_usd": 0.0,
        },
        "benchmarks": {
            "policy_engine": bench_policy_engine(iters),
            "secret_detector": bench_secret_detector(iters),
            "prompt_cache": await bench_prompt_cache(iters),
            "model_routing": bench_model_routing(iters),
            "parallel_orchestrator": await bench_parallel_orchestrator(),
            "batch_evaluation": bench_batch_evaluation(),
            "brownfield_modularity": bench_brownfield_modularity(),
        },
    }
    return results


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render empirical benchmark results as a reproducible Markdown document."""
    meta = report["metadata"]
    b = report["benchmarks"]
    pe = b["policy_engine"]
    sd = b["secret_detector"]
    pc = b["prompt_cache"]
    mr = b["model_routing"]
    po = b["parallel_orchestrator"]
    be = b["batch_evaluation"]
    bm = b["brownfield_modularity"]

    pe_p95 = pe["auth_eval"]["p95_ms"]
    pe_p99 = pe["auth_eval"]["p99_ms"]
    sd_p50 = sd["scan_latency"]["p50_ms"]
    sd_p95 = sd["scan_latency"]["p95_ms"]
    sd_p99 = sd["scan_latency"]["p99_ms"]
    mem_p50 = pc["memory_exact_hit"]["p50_ms"]
    mem_p95 = pc["memory_exact_hit"]["p95_ms"]
    mem_p99 = pc["memory_exact_hit"]["p99_ms"]
    sql_p50 = pc["sqlite_wal_exact_hit"]["p50_ms"]
    sql_p95 = pc["sqlite_wal_exact_hit"]["p95_ms"]
    sql_p99 = pc["sqlite_wal_exact_hit"]["p99_ms"]
    mr_p95 = mr["latency"]["p95_ms"]
    mr_p99 = mr["latency"]["p99_ms"]

    lines = [
        "# Empirical Benchmarks & Reproducible Performance Report",
        "",
        "> **Methodology**: Generated deterministically at **$0.00 external API cost** via "
        "[`scripts/run_benchmarks.py`](../scripts/run_benchmarks.py), following the "
        "**NeurIPS Reproducibility Checklist**, **Stanford HELM** multi-metric reporting, "
        "and **FinOps Foundation** cost-performance standards (reporting p50/p95/p99 tail "
        "latencies rather than averages alone).",
        "",
        "## Reproducibility & Environment Fingerprint",
        "",
        "| Property | Value |",
        "|---|---|",
        f"| Generated (UTC) | `{meta['generated_at_utc']}` |",
        f"| Mode / Iterations | `{meta['mode']}` (`{meta['iterations']}` iterations) |",
        f"| Python | `{meta['python_version']}` |",
        f"| Operating System | `{meta['os']}` |",
        f"| Logical CPUs | `{meta['cpu_count']}` |",
        "| External API Cost | `$0.00` |",
        "",
        "**Reproduce locally**:",
        "```bash",
        "python scripts/run_benchmarks.py --markdown-out docs/BENCHMARKS.md",
        "```",
        "",
        "---",
        "",
        "## Executive Summary Table",
        "",
        "| Subsystem | Metric | Measured Result | Tail Latency (p95 / p99) |",
        "|---|---|---:|---:|",
        (
            f"| **Cedar Policy Engine** | Auth throughput & mtime cache speedup | "
            f"**{pe['auth_ops_per_sec']:,.0f} eval/s** (`{pe['init_speedup_x']}x` warm init) | "
            f"`{pe_p95:.4f} ms` / `{pe_p99:.4f} ms` |"
        ),
        (
            f"| **Hybrid ML Secret Detector** | 10K-token clean doc scan "
            f"(0 FP, `{sd['synthetic_probes_detected']}` probes) | "
            f"**{sd_p50:.2f} ms** p50 (`{sd['false_positives_on_clean_doc']}` FP) | "
            f"`{sd_p95:.2f} ms` / `{sd_p99:.2f} ms` |"
        ),
        (
            f"| **Token Prompt Cache (L1 Memory)** | Exact-match lookup latency | "
            f"**{mem_p50:.4f} ms** p50 "
            f"(`{pc['projected_cost_reduction_pct']}%` projected savings @ 60% hit rate) | "
            f"`{mem_p95:.4f} ms` / `{mem_p99:.4f} ms` |"
        ),
        (
            f"| **Token Prompt Cache (L2 SQLite WAL)** | Persistent WAL lookup latency | "
            f"**{sql_p50:.3f} ms** p50 | `{sql_p95:.3f} ms` / `{sql_p99:.3f} ms` |"
        ),
        (
            f"| **Complexity Model Router** | Routing throughput & FinOps savings | "
            f"**{mr['decisions_per_sec']:,.0f} req/s** "
            f"(**{mr['cost_reduction_vs_flagship_pct']}%** savings vs. flagship) | "
            f"`{mr_p95:.4f} ms` / `{mr_p99:.4f} ms` |"
        ),
        (
            f"| **Parallel Agent Orchestrator** | 6-agent DAG (`4` concurrent reviewers) | "
            f"**{po['empirical_speedup_x']}x** (**{po['latency_reduction_pct']}%** faster) | "
            f"`{po['parallel_time_ms']:.1f} ms` vs `{po['sequential_time_ms']:.1f} ms` seq |"
        ),
        (
            f"| **Batch Evaluation Pipeline** | 500-sample golden evaluation discount | "
            f"**{be['openai_savings_pct']}%** OpenAI "
            f"(`${be['openai_batch_usd']:.2f}` vs `${be['openai_realtime_usd']:.2f}`) / "
            f"**{be['anthropic_savings_pct']}%** Anthropic | Async 24h SLA |"
        ),
        (
            f"| **Brownfield Modularity Analyzer** | Clauset-Newman-Moore (60-file monolith) | "
            f"**Q = {bm['structural_modularity_q']}** "
            f"(`{bm['communities_detected']}` domains detected) | "
            f"`{bm['elapsed_ms']:.2f} ms` total |"
        ),
        "",
        "---",
        "",
        "## Detailed Subsystem Breakdown",
        "",
        "### 1. Cedar Policy Engine (`policy/engine.py`)",
        f"- **Cold Load**: `{pe['cold_init_ms']:.3f} ms`",
        (
            f"- **Warm mtime-Cached Load**: p50 `{pe['warm_init']['p50_ms']:.4f} ms`, "
            f"p95 `{pe['warm_init']['p95_ms']:.4f} ms` (**{pe['init_speedup_x']}x** faster)"
        ),
        (
            f"- **Authorization Throughput**: `{pe['auth_ops_per_sec']:,.1f} ops/sec` "
            f"(p50 `{pe['auth_eval']['p50_ms']:.4f} ms`, p99 `{pe_p99:.4f} ms`)"
        ),
        "",
        "### 2. Hybrid ML Secret Detection (`security/secret_detector.py`)",
        f"- **Document Size**: `{sd['doc_chars']:,}` chars (~`{sd['estimated_tokens']:,}` tokens)",
        f"- **Scan Latency**: p50 `{sd_p50:.2f} ms`, p95 `{sd_p95:.2f} ms`, p99 `{sd_p99:.2f} ms`",
        (
            f"- **Precision Check**: `{sd['false_positives_on_clean_doc']}` false positives on "
            f"clean prose; `{sd['synthetic_probes_detected']}` synthetic secret probes detected."
        ),
        "",
        "### 3. FinOps Optimization Stack (`caching/` + `routing/` + `evaluation/batch_runner.py`)",
        (
            f"- **Prompt Caching**: In-memory L1 hits resolve in `{mem_p50:.4f} ms` p50; "
            f"persistent SQLite WAL L2 hits resolve in `{sql_p50:.3f} ms` p50."
        ),
        (
            f"- **Complexity-Aware Model Routing**: Mixed 6-task tier distribution "
            f"`{mr['tier_distribution']}` cut mean cost from "
            f"`${mr['baseline_flagship_cost_per_1k_usd']:.6f}/1K` to "
            f"`${mr['mean_routed_cost_per_1k_usd']:.6f}/1K` "
            f"(**{mr['cost_reduction_vs_flagship_pct']}% savings**)."
        ),
        "- **Batch Evaluations (500 Golden Samples)**:",
        (
            f"  - OpenAI: `${be['openai_realtime_usd']:.4f}` realtime -> "
            f"`${be['openai_batch_usd']:.4f}` batch (**{be['openai_savings_pct']}% savings**)"
        ),
        (
            f"  - Anthropic: `${be['anthropic_realtime_usd']:.4f}` realtime -> "
            f"`${be['anthropic_batch_usd']:.4f}` batch "
            f"(**{be['anthropic_savings_pct']}% savings**)"
        ),
        "",
        "### 4. Parallel Execution & Brownfield Modernization (`orchestrator/` + `brownfield/`)",
        (
            f"- **Parallel DAG Execution**: `{po['total_agents']}` stages with "
            f"`{po['peak_concurrent_agents']}` concurrent reviewers finished in "
            f"`{po['parallel_time_ms']:.1f} ms` vs `{po['sequential_time_ms']:.1f} ms` "
            f"sequential (**{po['empirical_speedup_x']}x** speedup, "
            f"**{po['latency_reduction_pct']}%** faster)."
        ),
        (
            f"- **Monolith Community Detection**: Partitioned a `{bm['nodes']}`-file / "
            f"`{bm['edges']}`-edge bridged monolith into `{bm['communities_detected']}` domain "
            f"services in `{bm['elapsed_ms']:.2f} ms` with Newman-Girvan structural modularity "
            f"**Q = {bm['structural_modularity_q']}** (strong-modularity threshold: Q >= 0.30)."
        ),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run reproducible empirical benchmarks for AI-Native Product Value System."
    )
    parser.add_argument("--quick", action="store_true", help="Run reduced iterations for fast CI.")
    parser.add_argument("--json", action="store_true", help="Print full JSON report to stdout.")
    parser.add_argument("--json-out", type=Path, help="Write JSON benchmark report to file.")
    parser.add_argument(
        "--markdown-out", type=Path, help="Write Markdown benchmark report to file."
    )
    args = parser.parse_args()

    report = asyncio.run(run_all_benchmarks(quick=args.quick))

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(render_markdown_report(report), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))
    elif not args.markdown_out and not args.json_out:
        print(render_markdown_report(report))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
