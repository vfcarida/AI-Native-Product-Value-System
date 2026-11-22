# Empirical Benchmarks & Reproducible Performance Report

> **Methodology**: Generated deterministically at **$0.00 external API cost** via [`scripts/run_benchmarks.py`](../scripts/run_benchmarks.py), following the **NeurIPS Reproducibility Checklist**, **Stanford HELM** multi-metric reporting, and **FinOps Foundation** cost-performance standards (reporting p50/p95/p99 tail latencies rather than averages alone).

## Reproducibility & Environment Fingerprint

| Property | Value |
|---|---|
| Generated (UTC) | `2026-11-22T14:04:24.672984+00:00` |
| Mode / Iterations | `standard` (`200` iterations) |
| Python | `3.12.10` |
| Operating System | `Windows 11` |
| Logical CPUs | `8` |
| External API Cost | `$0.00` |

**Reproduce locally**:
```bash
python scripts/run_benchmarks.py --markdown-out docs/BENCHMARKS.md
```

---

## Executive Summary Table

| Subsystem | Metric | Measured Result | Tail Latency (p95 / p99) |
|---|---|---:|---:|
| **Cedar Policy Engine** | Auth throughput & mtime cache speedup | **375 eval/s** (`17.1x` warm init) | `3.4884 ms` / `4.5354 ms` |
| **Hybrid ML Secret Detector** | 10K-token clean doc scan (0 FP, `2/2` probes) | **2.48 ms** p50 (`0` FP) | `2.94 ms` / `2.94 ms` |
| **Token Prompt Cache (L1 Memory)** | Exact-match lookup latency | **0.0105 ms** p50 (`60.0%` projected savings @ 60% hit rate) | `0.0114 ms` / `0.0185 ms` |
| **Token Prompt Cache (L2 SQLite WAL)** | Persistent WAL lookup latency | **28.443 ms** p50 | `30.698 ms` / `31.915 ms` |
| **Complexity Model Router** | Routing throughput & FinOps savings | **4,777 req/s** (**90.8%** savings vs. flagship) | `0.2513 ms` / `0.3286 ms` |
| **Parallel Agent Orchestrator** | 6-agent DAG (`4` concurrent reviewers) | **2.0x** (**50.0%** faster) | `141.0 ms` vs `282.0 ms` seq |
| **Batch Evaluation Pipeline** | 500-sample golden evaluation discount | **50.0%** OpenAI (`$1.75` vs `$3.50`) / **30.0%** Anthropic | Async 24h SLA |
| **Brownfield Modularity Analyzer** | Clauset-Newman-Moore (60-file monolith) | **Q = 0.7336** (`4` domains detected) | `7.78 ms` total |

---

## Detailed Subsystem Breakdown

### 1. Cedar Policy Engine (`policy/engine.py`)
- **Cold Load**: `9.139 ms`
- **Warm mtime-Cached Load**: p50 `0.5338 ms`, p95 `0.6735 ms` (**17.1x** faster)
- **Authorization Throughput**: `374.6 ops/sec` (p50 `2.4688 ms`, p99 `4.5354 ms`)

### 2. Hybrid ML Secret Detection (`security/secret_detector.py`)
- **Document Size**: `68,040` chars (~`17,010` tokens)
- **Scan Latency**: p50 `2.48 ms`, p95 `2.94 ms`, p99 `2.94 ms`
- **Precision Check**: `0` false positives on clean prose; `2/2` synthetic secret probes detected.

### 3. FinOps Optimization Stack (`caching/` + `routing/` + `evaluation/batch_runner.py`)
- **Prompt Caching**: In-memory L1 hits resolve in `0.0105 ms` p50; persistent SQLite WAL L2 hits resolve in `28.443 ms` p50.
- **Complexity-Aware Model Routing**: Mixed 6-task tier distribution `{'low': 101, 'medium': 66, 'high': 33}` cut mean cost from `$0.030000/1K` to `$0.002766/1K` (**90.8% savings**).
- **Batch Evaluations (500 Golden Samples)**:
  - OpenAI: `$3.5000` realtime -> `$1.7500` batch (**50.0% savings**)
  - Anthropic: `$4.8000` realtime -> `$3.3600` batch (**30.0% savings**)

### 4. Parallel Execution & Brownfield Modernization (`orchestrator/` + `brownfield/`)
- **Parallel DAG Execution**: `6` stages with `4` concurrent reviewers finished in `141.0 ms` vs `282.0 ms` sequential (**2.0x** speedup, **50.0%** faster).
- **Monolith Community Detection**: Partitioned a `60`-file / `183`-edge bridged monolith into `4` domain services in `7.78 ms` with Newman-Girvan structural modularity **Q = 0.7336** (strong-modularity threshold: Q >= 0.30).
