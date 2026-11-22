# Empirical Benchmarks & Reproducible Performance Report

> **Methodology**: Generated deterministically at **$0.00 external API cost** via [`scripts/run_benchmarks.py`](../scripts/run_benchmarks.py), following the **NeurIPS Reproducibility Checklist**, **Stanford HELM** multi-metric reporting, and **FinOps Foundation** cost-performance standards (reporting p50/p95/p99 tail latencies rather than averages alone).

## Reproducibility & Environment Fingerprint

| Property | Value |
|---|---|
| Generated (UTC) | `2026-11-22T15:34:21.253746+00:00` |
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
| **Cedar Policy Engine** | Auth throughput & mtime cache speedup | **163,840 eval/s** (`32.1x` warm init) | `0.0062 ms` / `0.0129 ms` |
| **Hybrid ML Secret Detector** | 10K-token clean doc scan (0 FP, `2/2` probes) | **2.59 ms** p50 (`0` FP) | `3.18 ms` / `3.18 ms` |
| **Token Prompt Cache (L1 Memory)** | Exact-match lookup latency | **0.0096 ms** p50 (`60.0%` projected savings @ 60% hit rate) | `0.0278 ms` / `0.0433 ms` |
| **Token Prompt Cache (L2 SQLite WAL)** | Persistent WAL lookup latency | **0.276 ms** p50 | `0.523 ms` / `0.817 ms` |
| **Complexity Model Router** | Routing throughput & FinOps savings | **4,551 req/s** (**90.8%** savings vs. flagship) | `0.2791 ms` / `0.3545 ms` |
| **Parallel Agent Orchestrator** | 6-agent DAG (`4` concurrent reviewers) | **2.0x** (**50.0%** faster) | `141.0 ms` vs `282.0 ms` seq |
| **Batch Evaluation Pipeline** | 500-sample golden evaluation discount | **50.0%** OpenAI (`$1.75` vs `$3.50`) / **30.0%** Anthropic | Async 24h SLA |
| **Brownfield Modularity Analyzer** | Clauset-Newman-Moore (60-file monolith) | **Q = 0.7336** (`4` domains detected) | `9.23 ms` total |

---

## Detailed Subsystem Breakdown

### 1. Cedar Policy Engine (`policy/engine.py`)
- **Cold Load**: `15.664 ms`
- **Warm mtime-Cached Load**: p50 `0.4885 ms`, p95 `0.6962 ms` (**32.1x** faster)
- **Authorization Throughput**: `163,840.4 ops/sec` (p50 `0.0056 ms`, p99 `0.0129 ms`)

### 2. Hybrid ML Secret Detection (`security/secret_detector.py`)
- **Document Size**: `68,040` chars (~`17,010` tokens)
- **Scan Latency**: p50 `2.59 ms`, p95 `3.18 ms`, p99 `3.18 ms`
- **Precision Check**: `0` false positives on clean prose; `2/2` synthetic secret probes detected.

### 3. FinOps Optimization Stack (`caching/` + `routing/` + `evaluation/batch_runner.py`)
- **Prompt Caching**: In-memory L1 hits resolve in `0.0096 ms` p50; persistent SQLite WAL L2 hits resolve in `0.276 ms` p50.
- **Complexity-Aware Model Routing**: Mixed 6-task tier distribution `{'low': 101, 'medium': 66, 'high': 33}` cut mean cost from `$0.030000/1K` to `$0.002766/1K` (**90.8% savings**).
- **Batch Evaluations (500 Golden Samples)**:
  - OpenAI: `$3.5000` realtime -> `$1.7500` batch (**50.0% savings**)
  - Anthropic: `$4.8000` realtime -> `$3.3600` batch (**30.0% savings**)

### 4. Parallel Execution & Brownfield Modernization (`orchestrator/` + `brownfield/`)
- **Parallel DAG Execution**: `6` stages with `4` concurrent reviewers finished in `141.0 ms` vs `282.0 ms` sequential (**2.0x** speedup, **50.0%** faster).
- **Monolith Community Detection**: Partitioned a `60`-file / `183`-edge bridged monolith into `4` domain services in `9.23 ms` with Newman-Girvan structural modularity **Q = 0.7336** (strong-modularity threshold: Q >= 0.30).
