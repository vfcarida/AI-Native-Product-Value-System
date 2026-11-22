# Release Notes: v1.0.0 (Multi-Provider, Polyglot Drift, Cloud-Native & Hardened Control Plane)

**Date**: 2026-11-22  
**Status**: v1.0 Reference Implementation (Production-Hardened Architecture, Zero-Warning Local & CI Suite)

## Highlights Since v0.1.0

1. **Multi-Provider LLM & Batch Evaluation Stack**:
   - Native `GeminiModelAdapter`, `AzureOpenAIModelAdapter`, and `OllamaModelAdapter` alongside `OpenAI`, `Anthropic`, and `Mock`, wired through `create_adapter()` and CLI `--provider` / `--model` flags (`pvs run`, `pvs eval`, `pvs review`).
   - 50% discounted `GeminiBatchAdapter` and `AzureOpenAIBatchAdapter` integrated into `BatchEvalRunner`, plus turnkey 5-provider FinOps routing catalog (`config/routing_multiprovider.yaml`).
2. **Polyglot AST Drift Detection**:
   - Deterministic structural TypeScript/JavaScript (`TypeScriptScanner`) and Go (`GoScanner`) AST drift scanning integrated end-to-end into `CodeScanner`, `SpecScanner`, and `scan_drift()`.
3. **Production-Hardened Governance, Persistence & Cloud-Native Deployment**:
   - Optional in-process Rust `cedarpy` PyO3 authorization backend (`cedarpy_rust` → `cedar_cli` → `python_fallback`) with 100% (`25/25`) verified parity and fail-closed numeric context guards.
   - Async PostgreSQL persistence backend (`PostgresDatabase`, `PostgresValueStreamRepository`, `create_database()`, `migrations/postgres/001_initial_schema.sql`) alongside WAL-mode SQLite.
   - Kubernetes Helm v3 chart (`deploy/helm/product-value-system/`, chart `1.1.0`) enforcing Restricted PSS, `NetworkPolicy`, HPA, PDB, `ServiceMonitor`, optional hardened companion OpenTelemetry Collector, and Grafana sidecar dashboard ConfigMap.
   - Deterministic supply-chain lockfile (`requirements.lock`, `scripts/generate-lockfile.py --check`) and expanded CI (`helm lint --strict`, `kubeconform`, `pip-audit`, `detect_secrets src/ config/ deploy/`).
4. **Complete Evolution Stack (`EVO-001`–`EVO-033`) & Continuous Architectural Hardening**:
   - **Contracts & Resilience**: 100% (`13/13`) strict Pydantic v2 validation for `config/*.yaml`, OWASP `*_FILE` secret expansion, AWS Builders' Library `CircuitBreaker` + `RetryTokenBucket` + full-jitter retries.
   - **FinOps & Performance**: Tiered exact + semantic `PromptCache`, 6-feature `ModelRouter`, `ParallelExecutor` DAG orchestration, `mtime_ns`-cached Cedar policy init, and reproducible benchmark harness (`docs/BENCHMARKS.md`).
   - **Security & DevX**: Default-deny `iptables` sandbox egress allowlists, Hybrid ML secret detection, constant-time `hmac.compare_digest` auth, OWASP HTTP security headers, handler-attached secret-redacting JSON logs, Git forensics & Clauset-Newman-Moore Strangler Fig modernization, non-punitive SPACE/DORA DevX telemetry, and `pvs doctor`/`route`/`brownfield`/`devx` CLI commands.

---

# Release Notes: v0.1.0

**Release Date**: 2026-11-18  
**Status**: Initial Release (Reference Implementation)

---

## Overview

The **AI-native Product Value System v0.1.0** is a reference implementation demonstrating AI-native product development from problem → outcome → discovery → specification → build → review → release → learn.

**This is a reference implementation for educational and research purposes.** It is NOT production-ready software.

---

## What's Included

### Core System

- **Value Stream Orchestrator**: Coordinates agents through the complete product development lifecycle
- **Agent System**: Modular agents (Discovery, Specification, Build, Architecture Review, Security Review)
- **Harness System**: Context, tools, memory, budget enforcement, checkpoints, and guardrails for agents
- **Policy Engine**: Cedar-based declarative authorization for agent actions
- **Sandbox Runtime**: Docker-based isolated execution
- **Evaluation Framework**: Deterministic + AI-based judges for quality gates
- **Observability**: OpenTelemetry GenAI instrumentation with cost tracking and secret redaction
- **Persistence & CLI**: SQLite persistence (`pvs.db`) and `pvs` command-line interface

### Agents

1. **Discovery Agent**: Analyzes problems and generates measurable outcome hypotheses
2. **Specification Agent**: Produces detailed specifications with functional/non-functional requirements and acceptance criteria
3. **Architecture Review Agent**: Reviews specifications for architectural soundness and scalability
4. **Security Review Agent**: Reviews specifications for security risks and compliance controls
5. **Build Agent**: Implements specifications and executes unit tests in an isolated sandbox

### Security

- Cedar policy engine for least-privilege authorization
- Docker sandbox with non-root user (`UID 1000`), dropped Linux capabilities, and resource limits
- Input validation, path traversal prevention, command sanitization, and output sanitization
- Secret detection (pre-commit Git hook, CI workflow, and OpenTelemetry span sanitization)
- Comprehensive threat model documentation covering OWASP LLM Top 10 ([docs/THREAT_MODEL.md](docs/THREAT_MODEL.md))

### Documentation

- Architecture documentation with 8 Mermaid C4/Sequence/Trust/Data-flow diagrams ([docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/architecture/DIAGRAMS.md](docs/architecture/DIAGRAMS.md))
- API, Configuration, Schema, and CLI Reference ([docs/reference/README.md](docs/reference/README.md))
- Task-oriented How-To Guides and Troubleshooting ([docs/howto/README.md](docs/howto/README.md))
- Threat model and secret management documentation ([docs/THREAT_MODEL.md](docs/THREAT_MODEL.md), [docs/SECRET_MANAGEMENT.md](docs/SECRET_MANAGEMENT.md))
- Complete greenfield and brownfield examples ([examples/](examples/))

---

## Requirements

- **Python**: 3.12 or higher
- **Docker**: Docker Desktop or Docker Engine
- **Git**: For artifact versioning and pre-commit secret scanning
- **Model API**: OpenAI API key (or `--mock` for local deterministic runs)

---

## Installation

```bash
# Clone repository
git clone https://github.com/vfcarida/AI-Native-Product-Value-System.git
cd AI-Native-Product-Value-System

# Install dependencies
pip install -e ".[dev]"

# Install Git hooks (secret scanning)
./scripts/install-git-hooks.sh

# Set API key (optional when using --mock)
export OPENAI_API_KEY="sk-..."

# Run example
make demo
```

---

## Quick Start

```bash
# Run greenfield example (payment flow)
pvs run examples/greenfield/problem_payment_flow.yaml

# Run brownfield example (legacy API documentation)
pvs run examples/brownfield/problem_document_legacy.yaml

# View previous runs
pvs list

# Show run details
pvs show <run-id>
```

---

## Known Limitations

### MVP Scope

- **Single-machine deployment**: No distributed orchestration
- **SQLite persistence**: Single-node SQLite database (`pvs.db`)
- **Local execution only**: No cloud deployment
- **Synthetic data only**: Designed for synthetic/non-PII workloads

### Not Production-Ready

- No formal external security audit (reference implementation only)
- No SLA or support guarantees
- No high-availability or disaster recovery

### Security Considerations

- Docker container isolation (not microVMs such as Firecracker/gVisor)
- Regex-based secret detection (not ML/entropy-hybrid)
- No global distributed rate limiting

See [THREAT_MODEL.md](docs/THREAT_MODEL.md) for detailed security analysis.

---

## Performance Baseline

**Greenfield Example** (`examples/greenfield/problem_payment_flow.yaml`):

- **Mock Mode**: `$0.00`, `<1.5s` end-to-end
- **Live GPT-4 Estimate**: `~$2.45`, `~15m 32s` across Discovery → Specification → Review → Build

**Test Suite** (`pytest tests/ --cov=product_value_system`):

- **Total Tests**: 149 collected (`142 passed, 7 skipped` when Docker daemon / live OpenAI key are absent)
  - Unit tests (`tests/unit/`): 79 passed, 1 skipped
  - Integration tests (`tests/integration/`): 30 passed, 6 skipped
  - Evaluation tests (`tests/eval/`): 7 passed
  - Security tests (`tests/security/`): 26 passed
- **Test Coverage**: `79%` across `src/product_value_system`
- **Total Duration**: `1m 10s`

---

## Breaking Changes

**N/A** (initial release)

---

## Deprecations

**N/A** (initial release)

---

## Contributors

- AI-Native Product Value System Team

---

## License

Apache License 2.0

---

## Next Steps

### For Users

- Review [documentation](docs/)
- Try [examples](examples/)

### For Contributors

- Read [CONTRIBUTING.md](CONTRIBUTING.md)
- Review [architecture docs](docs/ARCHITECTURE.md)

---

## Roadmap (Future Versions)

**v0.2.0** (Q1 2027):

- Multi-agent collaboration
- MCP (Model Context Protocol) support
- Trajectory evaluations
- Enhanced knowledge integration

**v0.3.0** (Q2 2027):

- Cloud deployment (Kubernetes)
- PostgreSQL support
- Model gateway with routing
- Advanced observability dashboards

**v1.0.0** (Q3 2027):

- Stable contracts
- Conformance test suite
- Production hardening
- External security audit

---

**Thank you for using the AI-native Product Value System!**
