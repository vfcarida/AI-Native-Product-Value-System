# AI-Native Product Value System

[![Python](https://img.shields.io/badge/python-3.12+-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Status](https://img.shields.io/badge/status-alpha-orange)](#current-limitations)
[![Tests](https://img.shields.io/badge/tests-4--tier%20suite-brightgreen)](tests/README.md)

**Version**: 0.1.0  
**Status**: 🚧 Reference Implementation (Alpha — Educational & Research Use)

---

## Purpose

This is a **reference implementation** demonstrating AI-native product development from problem statement to validated implementation: `Problem → Discovery → Specification → Review → Build → Evaluation`.

It shows how to build **quality, security, and governance** into AI-accelerated product development using modular agents, declarative Cedar policies, isolated Docker sandboxes, and OpenTelemetry observability.

**⚠️ This is NOT production software.** It is an educational and research reference implementation with documented limitations below.

---

## Features

### Core Workflow
- ✅ **Outcome-Driven Development**: Transform problem statements into schema-validated, measurable outcome hypotheses
- ✅ **Synthetic Customer Simulation**: Optional TinyTroupe-powered persona simulation (`TinyTroupeAdapter`, `PersonaFactory`, `PopulationGenerator`, `SimulationValidator`, `ScenarioRunner`) integrated into `DiscoveryAgent` and `pvs run-simulation`
- ✅ **6-Stage AI Agent Pipeline + Multi-Agent Independent Review**: Discovery → Specification → Parallel 4-Perspective Review (Security, Performance, Maintainability, Architecture via Elephant-Goldfish pattern & conflict-resolving `ReviewSynthesizer`) → Build → Learn (`MeasurementAgent`)
- ✅ **Outcome Measurement & Feedback Loops**: Production metric adapters (SQLite/SQLAlchemy, Prometheus, Mock), statistical significance & guardrail evaluation (`OutcomeTracker`), cost-to-outcome attribution (`CostAttributor`), regression alerting (`FeedbackLoop`), and `pvs measure-outcome` CLI
- ✅ **Evaluation Framework, Golden Datasets & Trajectory Evaluation**: Multi-judge quality gates (`SchemaValidationJudge`, `RubricComplianceJudge`, `PythonSyntaxJudge`, `TestCoverageJudge`, `CompletenessJudge`, `ClarityJudge`, plus 4 trajectory judges), 180 curated HELM-structured golden samples (`golden_datasets/`), 25 exemplar golden trajectories (`golden_datasets/trajectories/`), `TrajectoryCapture` & `TrajectoryEvaluator`, `RegressionEvaluator` baseline drift detection, `pvs eval --with-trajectories`, and CI eval gates (`.github/workflows/eval-gate.yml`)
- ✅ **Value Stream Orchestrator & Persistence**: State-machine orchestration with checkpoints, SQLite persistence (`pvs.db`), and `pvs` CLI

### Infrastructure & Security
- ✅ **Docker Sandbox Isolation**: Isolated container execution (`pvs/agent-sandbox:latest`) running as non-root (`UID 1000`), with dropped Linux capabilities, memory/CPU/PID limits, and network isolation
- ✅ **Policy-Based Authorization**: Cedar policy schema (`policies/schema.cedarschema`) and rules (`policies/agent_authorization.cedar`) with a deterministic Python fallback evaluator
- ✅ **Security Controls**: Input/schema validation, path traversal prevention, command sanitization, regex secret detection (`pre-commit` hook + CI), and OWASP LLM Top 10 threat model
- ✅ **OpenTelemetry Observability, Production Monitoring & SLOs**: GenAI semantic-convention distributed tracing, 12 OpenTelemetry/Prometheus metrics (`PVSMetrics`), 8+ traditional & AI-specific SLIs/SLOs, error-budget policies, automated rollback (`ProductionMonitor`), `/health`, `/ready`, `/metrics`, `/slos` endpoints, 8 multi-window burn-rate Prometheus alert rules, Alertmanager routing, and 4 Grafana dashboards (`docker-compose.monitoring.yml`)

### Model Support & Streaming
- ✅ **OpenAI Adapter**: GPT-4, GPT-4 Turbo, GPT-4o, GPT-3.5 Turbo with real SSE streaming, retries, tool calling, and USD cost calculation
- ✅ **Anthropic Adapter**: Claude 3.5 Sonnet, Claude 3 Opus, Claude 3 Haiku with real typed SSE streaming, retries, tool-format translation, output secret redaction, and USD cost calculation
- ✅ **Mock Adapter**: Deterministic local testing and demos with zero API cost

### Extensibility
- ✅ **Hexagonal Architecture**: Swappable protocol-based adapters (`ModelAdapter`, `SandboxProvider`, `Judge`, `BaseAgent`)

---

## Current Limitations

This project is in active alpha development (`v0.1.0`). Known limitations:

### Model Providers
- **Google Gemini, Azure OpenAI, and local models (Ollama/vLLM)**: Planned for `v1.0` — not yet implemented. Only OpenAI, Anthropic, and Mock adapters exist in `src/product_value_system/adapters/`.

### Discovery & Evidence
- **Synthetic simulation disclaimer**: TinyTroupe customer simulation (`src/product_value_system/simulation/`) provides synthetic persona feedback (with a built-in local fallback when `tinytroupe` is not installed), NOT real human user research.
- **Single-source evidence**: `DiscoveryAgent` analyzes a single structured `problem.yaml` input and does not yet synthesize multi-source customer interviews or telemetry (planned in EVO-022 / EVO-023).

### Lifecycle Stages
- **External analytics required for live Learn stage**: Post-release outcome measurement (`src/product_value_system/learning/`) supports SQLite, PostgreSQL/MySQL (SQLAlchemy), Prometheus, and Mock adapters; live measurement requires an external metrics database or Prometheus instance.
- **No progressive delivery / experiment design yet**: Feature flags, canary rollouts, and A/B experiment design tooling are planned for `v0.4` (EVO-015, EVO-016).

### Evaluation & Testing
- **Trajectory & golden dataset evaluation**: 180 curated golden dataset samples (`golden_datasets/`), 25 exemplar golden trajectories (`golden_datasets/trajectories/`), `TrajectoryCapture`/`TrajectoryEvaluator`, and automated regression evaluation gates (`pvs eval --with-trajectories`, `.github/workflows/eval-gate.yml`) are implemented; real-time trajectory visualization and replay remain future enhancements.
- **Real integration suite requires explicit opt-in**: Standard CI runs fast mocked integration tests (`tests/integration/`); live LLM/Docker/Cedar tests (`tests/integration_real/`) run on-demand or nightly with `RUN_REAL_INTEGRATION_TESTS=1`.

### Security & Deployment Scale
- **Single-machine deployment**: Uses local SQLite (`pvs.db`) and Docker containers (not microVMs such as Firecracker/gVisor, nor Kubernetes).
- **Regex secret detection**: Secret scanning uses regex patterns (ML-based detection planned in EVO-024). Cedar policy / Python fallback synchronization is verified automatically via `scripts/validate-policy-sync.py` and CI (`.github/workflows/policy-validation.yml`).

See [Roadmap](#roadmap) for versioned plans and [THREAT_MODEL.md](docs/THREAT_MODEL.md) for security analysis.

---

## Model Providers

### Implemented
- ✅ **OpenAI** (`OpenAIModelAdapter` / `OpenAIAdapter` — GPT-4, GPT-4 Turbo, GPT-4o, GPT-3.5 Turbo)
  - File: [src/product_value_system/adapters/openai_adapter.py](src/product_value_system/adapters/openai_adapter.py) (381 lines)
  - Features: Real SSE streaming, 429 rate-limit retries, tool/function calling, USD cost tracking
- ✅ **Anthropic** (`AnthropicAdapter` / `AnthropicModelAdapter` — Claude 3.5 Sonnet, Claude 3 Opus, Claude 3 Haiku)
  - File: [src/product_value_system/adapters/anthropic_adapter.py](src/product_value_system/adapters/anthropic_adapter.py) (603 lines)
  - Features: Real typed SSE streaming, system prompt extraction, OpenAI-to-Anthropic tool conversion, 429 retries, output secret redaction, USD cost tracking
- ✅ **Mock** (`MockModelAdapter` — deterministic local tests and demos)
  - File: [src/product_value_system/adapters/mock_adapter.py](src/product_value_system/adapters/mock_adapter.py)

### Planned
- 📋 **Google Gemini** (`v1.0`)
- 📋 **Azure OpenAI** (`v1.0`)
- 📋 **Local Models via Ollama / vLLM** (`v1.0`)

### Configuration

See [config/model_adapters.yaml](config/model_adapters.yaml):

```yaml
default_adapter: anthropic

adapters:
  openai:
    api_key: ${OPENAI_API_KEY}
    model: gpt-4

  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-3-5-sonnet-20241022
```

### Adding New Providers

Implement the `@runtime_checkable` `ModelAdapter` protocol in [src/product_value_system/adapters/model_adapter.py](src/product_value_system/adapters/model_adapter.py) (`invoke()` and `stream()`). See [How-To: Integrate a New Model Provider](docs/howto/integrate-model-provider.md).

---

## Installation

### Prerequisites

- **Python 3.12+**
- **Git**
- **Docker** (Desktop or Engine — required for live container sandboxes; optional for mocked unit/demo runs)
- **Model API Key** (`OPENAI_API_KEY` or `ANTHROPIC_API_KEY` — optional when running with the built-in Mock adapter)

### Steps

```bash
# 1. Clone repository
git clone https://github.com/vfcarida/AI-Native-Product-Value-System.git
cd AI-Native-Product-Value-System

# 2. Install package and development dependencies
pip install -e ".[dev]"

# 3. Install Git pre-commit secret scanning hook
./scripts/install-git-hooks.sh

# 4. Build the isolated agent sandbox image (optional if Docker is offline)
make docker-build-sandbox

# 5. Optional: Set an API key (omit to run locally with the deterministic Mock adapter)
export OPENAI_API_KEY="sk-..."
# or
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Optional: Cedar CLI

For native Cedar CLI policy evaluation (Python fallback is active automatically when omitted):

```bash
# macOS
brew install cedar-policy/cedar/cedar-cli

# Linux (requires Rust/Cargo)
cargo install cedar-policy-cli
```

---

## Quick Start

```bash
# Run greenfield example (AcmeBank instant payment notifications)
pvs run examples/greenfield/problem_payment_flow.yaml

# Run brownfield example (AcmeBank legacy Customer API documentation)
pvs run examples/brownfield/problem_document_legacy.yaml

# Inspect persisted runs
pvs list
pvs show <run-id>

# Capture an execution trajectory and run golden dataset + trajectory regression eval
pvs run examples/greenfield/problem_payment_flow.yaml --capture-trajectory --output trajectory.json
pvs eval discovery --fast --subset-size 10 --with-trajectories --use-mock
```

**Expected Output**: Orchestrator executes problem analysis, discovery, specification, review, and build stages, storing run metadata in `pvs.db` and artifacts under `artifacts/runs/<problem-id>/`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  AI-Native Product Value System              │
│                                                              │
│  Problem → Discovery → Specification → Review → Build       │
│                    └─ Evaluation Quality Gates ─┘            │
└─────────────────────────────────────────────────────────────┘
        ↕                    ↕                    ↕
   [User Input]      [Model APIs]         [Docker Sandbox]
   [problem.yaml]    [OpenAI/Anthropic]   [Cedar Policies]
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/architecture/DIAGRAMS.md](docs/architecture/DIAGRAMS.md) for C4, sequence, trust-boundary, and data-flow diagrams.

---

## Documentation

- **[Architecture & Diagrams](docs/ARCHITECTURE.md)** — System design and 8 Mermaid diagrams
- **[API & Schema Reference](docs/reference/README.md)** — Public APIs, schemas, configuration, and CLI commands
- **[How-To Guides](docs/howto/README.md)** — Custom agents, Cedar policies, judges, model adapters, troubleshooting
- **[Testing Guide](tests/README.md)** — 4-tier test strategy, live integration setup, and cost caps
- **[Golden Dataset Curation Guide](golden_datasets/README.md)** — 180 HELM-structured golden samples, schema, curation CLI, and regression evaluation gates
- **[Multi-Agent Review Guide](docs/review-guide.md)** — Elephant-Goldfish 4-perspective review, conflict resolution, and `pvs review` CLI
- **[SLO & Monitoring Guides](docs/slo-guide.md)** — Production SLIs/SLOs, [Grafana Dashboards](docs/dashboards.md), [Alerting Runbook](docs/runbooks/alerting.md), and [Monitoring Operations](docs/operations/monitoring.md)
- **[Threat Model](docs/THREAT_MODEL.md)** — Security analysis (OWASP LLM Top 10)
- **[Security Testing Guide](docs/SECURITY_TESTING.md)** — Adversarial prompt injection & security tests
- **[Policy Synchronization](docs/POLICY_SYNC.md)** — Cedar policy vs. Python fallback sync validation
- **[Cost Management](docs/COST_MANAGEMENT.md)** — Per-run/daily/monthly budget guards and emergency stop
- **[Secret Management](docs/SECRET_MANAGEMENT.md)** — Secret scanning and redaction controls

---

## Development & Testing

```bash
# Run unit tests (fast, deterministic, $0)
make test-unit

# Run standard CI suite (unit, mocked integration, eval, security, $0)
make test

# Run real integration tests (live OpenAI/Anthropic APIs, Docker, Cedar)
RUN_REAL_INTEGRATION_TESTS=1 make test-integration-real

# Lint & static type check
make lint

# Validate Cedar policies
make validate-policies

# Build Docker images
make docker-build
make docker-build-sandbox
```

See [tests/README.md](tests/README.md) for test tiers, cost limits (`$0.05/test`, `$0.50/run`), and CI workflows.

---

## Roadmap

### v0.2 — Core Hardening
- ✅ Anthropic Claude adapter (EVO-001)
- ✅ Real SSE streaming for OpenAI & Anthropic adapters (EVO-002)
- ✅ Real integration test suite with cost guardrails (EVO-003)
- ✅ Honest README & capability alignment (EVO-004)
- ✅ Cedar policy / Python fallback sync validation (EVO-005)
- ✅ Adversarial prompt injection test suite (EVO-006)
- ✅ Enhanced cost budget guards & emergency stop (EVO-007)
- ✅ Comprehensive 4-tier testing guide (EVO-008)

### v0.3 — Simulation & Multi-Source Evidence
- ✅ TinyTroupe customer simulation (EVO-009)
- 📋 Customer interview & multi-source evidence synthesis (EVO-022, EVO-023)
- 📋 Automated baseline validation (EVO-019)

### v0.4 — Lifecycle Completion
- ✅ Learn stage & outcome measurement agent (EVO-010)
- ✅ Production SLOs, Prometheus/Grafana monitoring & automated rollback (EVO-014)
- 📋 Progressive delivery & feature flags (EVO-015)
- 📋 Experiment design & statistical rigor tooling (EVO-016)

### v0.5 — Evaluation & Continuous Quality
- ✅ Curated golden dataset evaluation suite (EVO-011)
- ✅ Multi-turn trajectory evaluations (EVO-012)
- ✅ Multi-agent independent review & conflict synthesis (EVO-013)
- 📋 Automated drift detection (EVO-017)

### v1.0 — Production Grade
- 📋 Token caching & cost optimization (EVO-025, EVO-026)
- 📋 ML-based secret detection (EVO-024)
- 📋 Google Gemini, Azure OpenAI, and local (Ollama/vLLM) model adapters
- 📋 Brownfield Git history analysis & Strangler Fig modernization (EVO-030, EVO-031)

**Status Legend**: ✅ Done · 🚧 In Progress · 📋 Planned

---

## Security

- **Report vulnerabilities**: See [SECURITY.md](SECURITY.md) and [docs/SECURITY.md](docs/SECURITY.md)
- **Threat model**: See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)

---

## Contributing & License

- **Contributing**: See [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- **Changelog & Releases**: See [CHANGELOG.md](CHANGELOG.md) and [RELEASE_NOTES.md](RELEASE_NOTES.md)
- **License**: [Apache License 2.0](LICENSE)

---

**Last Updated**: 2026-11-19
