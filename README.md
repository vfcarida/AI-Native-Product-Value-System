# AI-Native Product Value System

<div align="center">

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache--2.0-2F855A?style=for-the-badge)](LICENSE)
[![Status: Beta Reference](https://img.shields.io/badge/status-Beta%20Reference-DD6B20?style=for-the-badge)](#roadmap)
[![Cedar Policy Sync](https://img.shields.io/badge/Cedar%20Policy%20Sync-100%25%20(25%2F25)-6B46C1?style=for-the-badge)](docs/POLICY_SYNC.md)
[![Test Suite](https://img.shields.io/badge/tests-900%2B%204--Tier%20Suite-009688?style=for-the-badge)](tests/README.md)

**A research-backed, production-hardened reference implementation for governed, FinOps-optimized, AI-native software delivery — from problem discovery to verified release and post-launch outcome learning.**

[Quick Start](#quick-start) • [Architecture](#architecture) • [Empirical Benchmarks](#empirical-benchmarks) • [Zero-Cost Showcases](examples/showcases/README.md) • [CLI & SDK](#python-sdk--cli-examples) • [Documentation](#documentation)

</div>

---

## Executive & Technical Value Proposition

Most AI coding tools accelerate *code generation* while leaving architecture, governance, security, FinOps cost control, and business outcome verification behind. The **AI-Native Product Value System** demonstrates how to engineer a **deterministic control plane** around **probabilistic AI agents** across the complete product lifecycle:

> `Problem → Discovery & Evidence → Specification → Parallel Multi-Agent Review → Sandboxed Build → Progressive Release → Post-Launch Outcome Learning`

### Why Teams Adopt This Architecture
- **Deterministic Governance Over Autonomous Agents**: Every agent runs inside a governed `Harness` enforcing **Cedar least-privilege policies** (100% verified parity), **12 strict Pydantic v2 YAML config contracts**, **8 JSON Schema 2020-12 artifact contracts**, hard per-run/daily/monthly USD caps, and an instant `EMERGENCY_STOP` kill switch.
- **Measured FinOps & Latency Efficiency**: Combines **tiered exact + semantic prompt caching** (`10.6 µs` L1 hits), **6-feature complexity model routing** (**90.8% cost reduction** vs. always-flagship), **30%–50% discounted batch evaluations**, **AWS Builders' Library circuit breakers & full-jitter retries**, and **NetworkX DAG parallel execution** (**2.0x** speedup on concurrent review layers).
- **Zero-Trust Defense-in-Depth**: Enforces **default-deny Docker `iptables` egress allowlists**, **hybrid regex + ML/entropy secret detection** (`2.58 ms` p50 on 17K-token documents, 0 FP), **constant-time `hmac.compare_digest` auth**, OWASP security headers, and **handler-attached secret-redacting OpenTelemetry JSON logs**.
- **Enterprise Brownfield Modernization**: Prioritizes legacy refactoring via read-only **Git temporal forensics** (hotspots, temporal coupling, age, anonymized bus factor), partitions bridged monoliths using **Clauset-Newman-Moore greedy modularity** ($Q = 0.7336$), and generates **shadow-mode Anti-Corruption Layers** with sticky SHA-256 progressive Strangler Fig traffic cutovers (`1% → 100%`).
- **Non-Punitive SPACE & DORA DevX Telemetry**: Instruments cycle time, AI acceptance rate, deployments, and satisfaction surveys with enforced `aggregate_only: true` and `anonymize: true` privacy contracts exported to Prometheus (`/metrics`), Grafana, and `/devx`.

---

## Architecture

```mermaid
flowchart TB
    Dev["Product & Engineering Teams (Make / pvs CLI / Python SDK)"]

    subgraph Control["Deterministic Control Plane"]
        Orch["ValueStreamOrchestrator + ParallelExecutor (DAG)"]
        Contracts["12 Strict Pydantic YAML Contracts + 8 JSON Schemas"]
        Harness["Harness: Hard Budget + Guardrails + CircuitBreaker"]
        Policy["mtime-Cached Cedar PolicyEngine (100% Sync)"]
        DB["WAL SQLite Persistence & Checkpoints"]
    end

    subgraph FinOps["FinOps & Resilience Layer"]
        Router["Complexity ModelRouter (LOW / MED / HIGH)"]
        Cache["PromptCache (L1 Memory / L2 SQLite WAL / Redis)"]
        Batch["BatchEvaluationRunner (OpenAI 50% / Anthropic 30%)"]
    end

    subgraph Agents["Governed Agent & Modernization Plane"]
        Pipeline["Discovery | Spec | 4-Perspective Review | Build | Learn"]
        Brownfield["Brownfield: GitAnalyzer + CNM Modularity Q + Shadow ACL"]
        Sandbox["Docker Sandbox + iptables Egress Allowlist"]
        Secret["Hybrid ML Secret Detector (Regex + ML + Entropy)"]
    end

    subgraph Obs["Observability & Non-Punitive DevX"]
        OTel["OTel GenAI v1.28+ Tracing + Secret-Redacting JSON Logs"]
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
python -m product_value_system.cli.main doctor

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

# Verify all 12 YAML configs, 8 JSON schemas, and Cedar policy engine
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
import asyncio
from product_value_system import (
    CircuitBreaker,
    DevXTracker,
    ModelRouter,
    PolicyEngine,
    PromptCache,
    validate_all_configs,
)

# 1. Fail-fast startup validation across all 12 config/*.yaml contracts
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
PVS Doctor v0.1.0 — HEALTHY
  [OK] python_runtime: Python 3.12.10
  [OK] yaml_config_contracts: 12 configs valid
  [OK] cedar_policy_engine: Loaded agent_authorization.cedar (cached=False)
  [OK] json_schemas: 8 schema file(s) in schemas
  [OK] llm_provider_credentials: No live API keys set (offline mock mode ready at $0 cost)
```

---

## Empirical Benchmarks

Measured deterministically at **$0.00 external API cost** via [`scripts/run_benchmarks.py`](scripts/run_benchmarks.py) (200 iterations, reporting tail latencies p50/p95/p99 per NeurIPS / HELM / FinOps Foundation standards). Full methodology and environment fingerprint in **[docs/BENCHMARKS.md](docs/BENCHMARKS.md)**.

| Subsystem | Metric | Measured Result | Tail Latency (p95 / p99) |
|---|---|---:|---:|
| **Cedar Policy Engine** | Auth throughput & mtime-cache init speedup | **377 eval/s** (`14.6x` warm init) | `3.52 ms` / `3.98 ms` |
| **Hybrid ML Secret Detector** | 17K-token clean doc scan (0 FP, `2/2` probes) | **2.58 ms** p50 (`0` FP) | `3.59 ms` / `3.59 ms` |
| **Prompt Cache (L1 Memory)** | Exact-match lookup latency | **0.0106 ms** p50 (`60%` proj. savings) | `0.0113 ms` / `0.0185 ms` |
| **Complexity Model Router** | Routing throughput & FinOps savings | **4,404 req/s** (**90.8%** savings vs. flagship) | `0.31 ms` / `0.44 ms` |
| **Parallel Agent Orchestrator** | 6-agent DAG (`4` concurrent reviewers) | **2.0x** speedup (**50.0%** faster) | `141 ms` vs `282 ms` seq |
| **Batch Evaluation Pipeline** | 500-sample golden evaluation discount | **50.0%** OpenAI / **30.0%** Anthropic | Async 24h SLA |
| **Brownfield Modularity** | Clauset-Newman-Moore (60-file monolith) | **Q = 0.7336** (`4` domains detected) | `8.98 ms` total |

Reproduce on your hardware:
```bash
python scripts/run_benchmarks.py --markdown-out docs/BENCHMARKS.md
```

---

## Repository Structure

```text
AI-Native-Product-Value-System/
├── src/product_value_system/   # 31 public packages (agents, orchestrator, harness, policy,
│                               #   caching, routing, evaluation, sandbox, security, brownfield,
│                               #   metrics, monitoring, observability, contracts, api, cli, ...)
├── config/                     # 12 strictly validated YAML configs (StrictConfigModel)
├── schemas/                    # 8 JSON Schema 2020-12 artifact contracts
├── policies/                   # Declarative Cedar authorization schema & policies
├── examples/                   # Greenfield, brownfield, progressive delivery & $0 showcases
├── golden_datasets/            # 180 HELM-structured eval samples & 25 golden trajectories
├── docs/                       # Architecture, 6 ADRs, benchmarks, reference & 30+ guides
├── tests/                      # 4-tier test suite (unit + Hypothesis PBT, integration, eval, security)
├── scripts/                    # Benchmark harness, policy sync, secret scan & release audit
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
| **Observability, SLOs & DevX** | [OTel GenAI Conventions](docs/otel-genai-conventions.md) · [DevX SPACE/DORA Metrics](docs/devx-metrics.md) · [SLOs & Rollback](docs/slo-guide.md) · [Progressive Delivery](docs/progressive-delivery.md) · [Grafana Dashboards](docs/dashboards.md) |
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
- 📋 **v1.0 Planned**: Google Gemini, Azure OpenAI, and local Ollama/vLLM model adapters; multi-language (TypeScript/Go) AST drift detection; Kubernetes Helm chart deployment manifests.

---

## Security, Contributing & License

- **Security Policy & Vulnerability Reporting**: [SECURITY.md](SECURITY.md) and [docs/SECURITY.md](docs/SECURITY.md)
- **Contributing Guidelines**: [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- **Changelog & Release Notes**: [CHANGELOG.md](CHANGELOG.md) and [RELEASE_NOTES.md](RELEASE_NOTES.md)
- **License**: Distributed under the **[Apache License 2.0](LICENSE)**.

---

**Last Updated**: 2026-11-22
