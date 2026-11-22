# AI-Native Product Value System

<div align="center">

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache--2.0-2F855A?style=for-the-badge)](LICENSE)
[![Status: v1.0 Reference](https://img.shields.io/badge/status-v1.0%20Reference-2F855A?style=for-the-badge)](#roadmap)
[![Cedar Policy Sync](https://img.shields.io/badge/Cedar%20Policy%20Sync-100%25%20(25%2F25)-6B46C1?style=for-the-badge)](docs/POLICY_SYNC.md)
[![Test Suite](https://img.shields.io/badge/tests-900%2B%204--Tier%20Suite-009688?style=for-the-badge)](tests/README.md)

**A research-backed, production-hardened reference implementation for governed, FinOps-optimized, AI-native software delivery — from problem discovery to verified release and post-launch outcome learning.**

[Quick Start](#quick-start) • [Architecture](#architecture) • [Empirical Benchmarks](#empirical-benchmarks) • [Limitations & Non-Goals](#known-limitations--non-goals) • [CLI & SDK](#python-sdk--cli-examples) • [Documentation](#documentation)

</div>

---

## Executive & Technical Value Proposition

Most AI coding tools accelerate *code generation* while leaving architecture, governance, security, FinOps cost control, and business outcome verification behind. The **AI-Native Product Value System** demonstrates how to engineer a **deterministic control plane** around **probabilistic AI agents** across the complete product lifecycle:

> `Problem → Discovery & Evidence → Specification → Parallel Multi-Agent Review → Sandboxed Build → Progressive Release → Post-Launch Outcome Learning`

### Why Teams Adopt This Architecture
- **Deterministic Governance Over Autonomous Agents**: Every agent runs inside a governed `Harness` enforcing **Cedar least-privilege policies** (100% verified parity, optional in-process Rust `cedarpy` PyO3 backend), **13 strict Pydantic v2 YAML config contracts**, **8 JSON Schema 2020-12 artifact contracts**, hard per-run/daily/monthly USD caps, and an instant `EMERGENCY_STOP` kill switch.
- **Measured FinOps & Latency Efficiency**: Combines **tiered exact + semantic prompt caching** (`9.6 µs` L1 memory / `0.28 ms` L2 SQLite WAL hits), **6-feature complexity model routing** (**90.8% cost reduction** vs. always-flagship), **30%–50% discounted batch evaluations**, **AWS Builders' Library circuit breakers & full-jitter retries**, and **NetworkX DAG parallel execution** (**2.0x** speedup on concurrent review layers).
- **Zero-Trust Defense-in-Depth**: Enforces **default-deny Docker `iptables` egress allowlists**, **hybrid regex + ML/entropy secret detection** (`2.59 ms` p50 on 17K-token documents, 0 FP), **constant-time `hmac.compare_digest` auth**, OWASP security headers, and **handler-attached secret-redacting OpenTelemetry JSON logs**.
- **Enterprise Brownfield Modernization**: Prioritizes legacy refactoring via read-only **Git temporal forensics** (hotspots, temporal coupling, age, anonymized bus factor), partitions bridged monoliths using **Clauset-Newman-Moore greedy modularity** ($Q = 0.7336$), and generates **shadow-mode Anti-Corruption Layers** with sticky SHA-256 progressive Strangler Fig traffic cutovers (`1% → 100%`).
- **Non-Punitive SPACE & DORA DevX Telemetry**: Instruments cycle time, AI acceptance rate, deployments, and satisfaction surveys with enforced `aggregate_only: true` and `anonymize: true` privacy contracts exported to Prometheus (`/metrics`), Grafana, and `/devx`.

---

## Architecture

```mermaid
flowchart TB
    Dev["Product & Engineering Teams (Make / pvs CLI / Python SDK)"]

    subgraph Control["Deterministic Control Plane"]
        Orch["ValueStreamOrchestrator + ParallelExecutor (DAG)"]
        Contracts["13 Strict Pydantic YAML Contracts + 8 JSON Schemas"]
        Harness["Harness: Hard Budget + Guardrails + CircuitBreaker"]
        Policy["mtime-Cached Cedar PolicyEngine (cedarpy / CLI / Python)"]
        DB["WAL SQLite / Async PostgreSQL Persistence & Checkpoints"]
    end

    subgraph FinOps["FinOps & Resilience Layer"]
        Router["Complexity ModelRouter (LOW / MED / HIGH)"]
        Cache["PromptCache (L1 Memory / L2 SQLite WAL / Redis)"]
        Batch["BatchEvalRunner (OpenAI/Gemini/Azure 50% / Anthropic 30%)"]
    end

    subgraph Agents["Governed Agent & Modernization Plane"]
        Pipeline["Discovery | Spec | 4-Perspective Review | Build | Learn"]
        Brownfield["Brownfield: GitAnalyzer + CNM Modularity Q + Shadow ACL"]
        Sandbox["Docker Sandbox + iptables Egress Allowlist"]
        Secret["Hybrid ML Secret Detector (Regex + ML + Entropy)"]
    end

    subgraph Obs["Observability & Non-Punitive DevX"]
        OTel["OTel GenAI v1.28-v1.30+ Tracing + Secret-Redacting JSON Logs"]
        HTTP["FastAPI: /health/live, /health/ready, /slos, /metrics, /devx"]
        DevX["SPACE & DORA Tracker (aggregate_only=true)"]
    end

    Dev --> Orch
    Contracts --> Orch
    Orch --> Harness
    Harness --> Policy
    Harness --> Router
    Router --> Cache
    Orch --> Pipeline
    Orch --> DB
    Pipeline --> Sandbox
    Pipeline --> Secret
    Dev --> Brownfield
    Batch --> Pipeline
    Harness --> OTel
    Orch --> DevX
    DevX --> HTTP
    OTel --> HTTP
```

Explore full C4 (L1–L3), sequence, trust-boundary, and deployment diagrams in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/architecture/DIAGRAMS.md](docs/architecture/DIAGRAMS.md), and [docs/adr/README.md](docs/adr/README.md) (`ADR-0001` through `ADR-0006`).

---

## Quick Start

Get verified diagnostics and runnable demos in under **2 minutes** at **$0.00 external API cost**.

### Using Make (Recommended)

> **Platform Note**: Works natively on Linux and macOS; on Windows, use WSL/Git Bash or run the direct Python commands below in PowerShell.

```bash
# 1. Install dependencies, initialize .env from .env.example, and configure git hooks
make dev-setup

# 2. Run environment, config contract, Cedar policy & schema diagnostics
python -m product_value_system doctor

# 3. Run greenfield demo offline at $0 cost using deterministic Mock adapter
make demo USE_MOCK=1

# 4. Run zero-cost showcases (FinOps, Brownfield Modularity, Parallel DevX)
python examples/showcases/finops_optimization_demo.py
python examples/showcases/brownfield_modernization_demo.py
python examples/showcases/parallel_devx_demo.py

# 5. View all 23+ developer targets
make help
```

### Without Make (Direct Python / CLI)

```bash
git clone https://github.com/vfcarida/AI-Native-Product-Value-System.git
cd AI-Native-Product-Value-System
pip install -e ".[dev]"

# Verify all 13 YAML configs, 8 JSON schemas, and Cedar policy engine
pvs doctor --json

# Inspect FinOps model routing for any prompt
pvs route --prompt "Convert CSV rows to JSON schema" --task-type format_conversion

# Analyze git hotspots & Strangler Fig boundaries on any repo
pvs brownfield --repo . --days 90 --strangler

# Run full greenfield value stream (offline Mock mode or with OPENAI_API_KEY / ANTHROPIC_API_KEY)
pvs run examples/greenfield/problem_payment_flow.yaml
```

---

## Python SDK & CLI Examples

Top-level lazy exports (`product_value_system`) expose the core control plane directly:

```python
from product_value_system import (
    CircuitBreaker,
    DevXTracker,
    ModelRouter,
    PolicyEngine,
    PromptCache,
    validate_all_configs,
)

# 1. Fail-fast startup validation across all 13 config/*.yaml contracts
report = validate_all_configs("config", expand_env=True)
assert report.is_valid, report.errors

# 2. Complexity-aware FinOps model routing
router = ModelRouter.from_yaml("config/routing.yaml")
decision = router.route(
    [{"role": "user", "content": "Extract HTTP status codes from this log line."}],
    task_type="extraction",
)
print(f"Routed -> {decision.provider}/{decision.model} (tier={decision.complexity_level.value})")
```

Sample output from `pvs doctor`:
```text
PVS Doctor v1.0.0 — HEALTHY
  [OK] python_runtime: Python 3.12.10
  [OK] yaml_config_contracts: 13 configs valid
  [OK] cedar_policy_engine: Loaded agent_authorization.cedar (cached=False)
  [OK] json_schemas: 8 schema file(s) in schemas
  [OK] llm_provider_credentials: No live API keys set (offline mock mode ready at $0 cost)
```

---

## Empirical Benchmarks

Measured deterministically at **$0.00 external API cost** via [`scripts/run_benchmarks.py`](scripts/run_benchmarks.py) (200 iterations, reporting tail latencies p50/p95/p99 per NeurIPS / HELM / FinOps Foundation standards). Full methodology and environment fingerprint in **[docs/BENCHMARKS.md](docs/BENCHMARKS.md)**.

| Subsystem | Metric | Measured Result | Tail Latency (p95 / p99) |
|---|---|---:|---:|
| **Cedar Policy Engine** | Auth throughput & mtime-cache init speedup | **163,840 eval/s** (`32.1x` warm init) | `0.0062 ms` / `0.0129 ms` |
| **Hybrid ML Secret Detector** | 17K-token clean doc scan (0 FP, `2/2` probes) | **2.59 ms** p50 (`0` FP) | `3.18 ms` / `3.18 ms` |
| **Prompt Cache (L1 Memory)** | Exact-match lookup latency | **0.0096 ms** p50 (`60%` proj. savings) | `0.0278 ms` / `0.0433 ms` |
| **Prompt Cache (L2 SQLite WAL)** | Persistent WAL exact-match lookup | **0.276 ms** p50 (zero-write reads) | `0.523 ms` / `0.817 ms` |
| **Complexity Model Router** | Routing throughput & FinOps savings | **4,551 req/s** (**90.8%** savings vs. flagship) | `0.2791 ms` / `0.3545 ms` |
| **Parallel Agent Orchestrator** | 6-agent DAG (`4` concurrent reviewers) | **2.0x** speedup (**50.0%** faster) | `141.0 ms` vs `282.0 ms` seq |
| **Batch Evaluation Pipeline** | 500-sample golden evaluation discount | **50.0%** OpenAI / **30.0%** Anthropic | Async 24h SLA |
| **Brownfield Modularity** | Clauset-Newman-Moore (60-file monolith) | **Q = 0.7336** (`4` domains detected) | `9.23 ms` total |

Reproduce on your hardware:
```bash
python scripts/run_benchmarks.py --markdown-out docs/BENCHMARKS.md
```

---

## Known Limitations & Non-Goals

### Known Limitations
- **Optional Rust Cedar Engine**: `PolicyEngine` automatically uses in-process Rust `cedarpy` PyO3 bindings when installed (`pip install 'product-value-system[cedar]'`), falls back to the `cedar` CLI if on `PATH`, and otherwise runs its 100%-parity deterministic Python evaluator (`scripts/validate-policy-sync.py`: `25/25`).
- **Docker & Linux `iptables` Sandbox**: Container execution and `iptables` network egress allowlists require a running Docker daemon (Linux `NET_ADMIN` inside the sandbox container); environments without Docker run all control-plane, evaluation, and mock workflows locally without container execution.
- **Persistence Topology**: Local runs default to WAL-mode SQLite (`Database` / `SQLiteCacheBackend`). For multi-node Kubernetes deployments, configure a `postgresql://` DSN (`create_database()` / `PostgresDatabase`, `pip install 'product-value-system[postgres]'`) alongside `RedisCacheBackend`.
- **Deterministic `$0` Suite vs. Live LLM Variance**: The default test and benchmark suites exercise schemas, policies, routing, caching, and statistical logic deterministically (`MockModelAdapter`). Real LLM outputs and token costs vary by model snapshot; opt in to live provider checks via `RUN_REAL_INTEGRATION_TESTS=1` (capped at `$0.05/test`, `$0.50/run`).
- **Graceful Degradation for Heavy Optional Extras**: Microsoft Presidio/spaCy NER (`secret_detection`) and `TinyTroupe` (`simulation`) fall back to deterministic heuristic/entropy implementations when optional extras are not installed.

### Explicit Non-Goals
- **Not a Hosted SaaS or Chat GUI**: Designed as a CLI-first, embeddable Python control plane and library; bundled FastAPI apps expose operational `/health/*`, `/metrics`, `/devx`, and feature-flag admin endpoints only.
- **Not Individual Developer Surveillance**: DevX SPACE/DORA instrumentation enforces `aggregate_only: true`, SHA-256 anonymization, and explicit developer opt-out by design.
- **No Opaque Prompt-Chain Magic**: Every stage, policy rule, budget cap, and artifact schema is declared explicitly in versioned YAML, JSON Schema, and Cedar files.

---

## Repository Structure

```text
AI-Native-Product-Value-System/
├── src/product_value_system/   # 30 public packages (agents, orchestrator, harness, policy,
│                               #   caching, routing, evaluation, sandbox, security, brownfield,
│                               #   metrics, monitoring, observability, contracts, api, cli, ...)
├── config/                     # 13 strictly validated YAML configs (StrictConfigModel)
├── schemas/                    # 8 JSON Schema 2020-12 artifact contracts
├── policies/                   # Declarative Cedar authorization schema & policies
├── examples/                   # Greenfield, brownfield, progressive delivery & $0 showcases
├── golden_datasets/            # 180 HELM-structured eval samples & 25 golden trajectories
├── docs/                       # Architecture, 6 ADRs, benchmarks, reference & 30+ guides
├── tests/                      # 4-tier test suite (unit + Hypothesis PBT, integration, eval, security)
├── scripts/                    # Benchmark harness, policy sync, secret scan & release audit
├── deploy/helm/                # Production-hardened Kubernetes Helm v3 chart (Restricted PSS)
├── Dockerfile                  # Hardened multi-stage non-root container (UID 10001 + HEALTHCHECK)
└── Makefile                    # 23+ self-documenting developer & CI workflow commands
```

---

## Documentation

| Category | Key Guides |
|---|---|
| **Architecture & ADRs** | [Architecture Guide](docs/ARCHITECTURE.md) · [C4 & Sequence Diagrams](docs/architecture/DIAGRAMS.md) · [ADR Index (`ADR-0001`–`0006`)](docs/adr/README.md) · [Empirical Benchmarks](docs/BENCHMARKS.md) |
| **API, Config & CLI** | [Reference Index](docs/reference/README.md) · [All 21 `pvs` CLI Commands](docs/reference/cli/commands.md) · [How-To Guides](docs/howto/README.md) · [Zero-Cost Showcases](examples/showcases/README.md) |
| **FinOps & Orchestration** | [Token Caching](docs/token-caching.md) · [Model Routing](docs/model-routing.md) · [Batch Evaluations](docs/batch-evaluations.md) · [Parallel Execution](docs/parallel-execution.md) · [Cost Management](docs/COST_MANAGEMENT.md) |
| **Security & Governance** | [Threat Model (OWASP LLM/Agentic)](docs/THREAT_MODEL.md) · [Network Egress Filtering](docs/network-filtering.md) · [Policy Sync](docs/POLICY_SYNC.md) · [Secret Management](docs/SECRET_MANAGEMENT.md) · [Security Testing](docs/SECURITY_TESTING.md) |
| **Brownfield & Engineering** | [Git History Analysis](docs/git-history-analysis.md) · [Strangler Fig Modernization](docs/strangler-fig-modernization.md) · [Drift Detection](docs/drift-detection.md) · [Spec-Kit Patterns](docs/spec-kit-guide.md) |
| **Observability, SLOs & DevX** | [OTel GenAI Conventions](docs/otel-genai-conventions.md) · [DevX SPACE/DORA Metrics](docs/devx-metrics.md) · [SLOs & Rollback](docs/slo-guide.md) · [Progressive Delivery](docs/progressive-delivery.md) · [Grafana Dashboards](docs/dashboards.md) · [Kubernetes Helm Chart](deploy/helm/product-value-system/README.md) |
| **Discovery & Validation** | [Evidence Synthesis](docs/evidence-synthesis.md) · [Interview Synthesis](docs/interview-synthesis.md) · [Experiment Design](docs/experiment-design.md) · [Baseline Validation](docs/baseline-validation.md) · [Golden Datasets](golden_datasets/README.md) |

---

## Development, Quality Gates & Testing

```bash
make lint                  # Ruff lint + strict mypy across 200+ source files
make test-unit             # Fast unit + Hypothesis property-based invariant tests ($0)
make test                  # Full 4-tier non-live suite (unit, integration, eval, security, $0)
make validate-policies     # Verify 100% Cedar / Python policy parity (25/25)
./scripts/final-audit.sh   # Full EVO-001..EVO-033 release readiness audit
```

See [tests/README.md](tests/README.md) for test tiers, live integration opt-in (`RUN_REAL_INTEGRATION_TESTS=1`), and hard per-test cost limits (`$0.05/test`, `$0.50/run`).

---

## Roadmap

- ✅ **v0.1 – v0.5 (Core, Simulation, Evaluation, Governance & Observability)**: Multi-provider adapters (`OpenAI`, `Anthropic`, `Mock`) with SSE streaming, Cedar policy engine, Docker sandboxes, TinyTroupe simulation, Learn-stage outcome measurement, 180-sample golden datasets + trajectory evals, 4-perspective multi-agent review, SLOs & feature flags, A/B power & baseline validation, spec/code drift detection, ADR manager, Spec-Kit, OTel GenAI v1.28+, interview & evidence synthesis.
- ✅ **v0.9 / Evolution P2 (EVO-024 – EVO-033 & 20-Round Production Hardening)**: Hybrid ML secret detection, L1/L2 WAL prompt caching, complexity model routing, discounted batch evaluations, DAG parallel execution, zero-trust `iptables` egress filtering, Git history forensics, Clauset-Newman-Moore Strangler Fig modernization + shadow ACL, SPACE/DORA DevX telemetry, strict Pydantic v2 config contracts (`12/12`), AWS resilience circuit breakers, constant-time auth & OWASP headers, structured secret-redacting logs, `pvs doctor`/`route`/`brownfield`/`devx` CLI, and reproducible benchmarks.
- ✅ **v1.0 Multi-Provider, Polyglot Drift & Kubernetes Readiness**: Native Google Gemini (`GeminiModelAdapter`), Azure OpenAI (`AzureOpenAIModelAdapter`), and local Ollama (`OllamaModelAdapter`) adapters with unified `create_adapter()` factory & CLI `--provider`/`--model` flags; multi-language TypeScript/JavaScript & Go structural AST drift scanning (`TypeScriptScanner`, `GoScanner`) integrated end-to-end into `CodeScanner`/`SpecScanner`/`scan_drift()`; Gemini & Azure OpenAI batch evaluation adapters (`50%` discount); optional in-process Rust `cedarpy` PyO3 bindings; async PostgreSQL persistence (`PostgresDatabase`, `create_database()`); deterministic `requirements.lock` + CI `helm lint`/`kubeconform`/`pip-audit`; and production-hardened Kubernetes Helm v3 chart (`deploy/helm/product-value-system/`) with Restricted PSS, `NetworkPolicy`, HPA, PDB, `ServiceMonitor`, optional companion OTel Collector, and Grafana dashboard ConfigMap.

---

## Security, Contributing & License

- **Security Policy & Vulnerability Reporting**: [SECURITY.md](SECURITY.md) and [docs/SECURITY.md](docs/SECURITY.md)
- **Contributing Guidelines**: [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- **Changelog & Release Notes**: [CHANGELOG.md](CHANGELOG.md) and [RELEASE_NOTES.md](RELEASE_NOTES.md)
- **License**: Distributed under the **[Apache License 2.0](LICENSE)**.

---

**Last Updated**: 2026-11-22
