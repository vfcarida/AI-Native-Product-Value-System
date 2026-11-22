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
- ✅ **Evaluation Framework, Golden Datasets, Trajectory & Batch Evaluations**: Multi-judge quality gates (`SchemaValidationJudge`, `RubricComplianceJudge`, `PythonSyntaxJudge`, `TestCoverageJudge`, `CompletenessJudge`, `ClarityJudge`, plus 4 trajectory judges), 180 curated HELM-structured golden samples (`golden_datasets/`), 25 exemplar golden trajectories (`golden_datasets/trajectories/`), `TrajectoryCapture` & `TrajectoryEvaluator`, `RegressionEvaluator` baseline drift detection, discounted OpenAI (50%) & Anthropic (30%) batch evaluations (`BatchEvalRunner`, `EvalRunner`, `BatchPoller`, `config/evaluation.yaml`), `pvs eval --with-trajectories`, and CI eval gates (`.github/workflows/eval-gate.yml`)
- ✅ **Value Stream Orchestrator, Parallel Execution & Persistence**: State-machine orchestration with DAG-based parallel agent execution (`DependencyGraph`, `ParallelExecutor`, `ResourceManager`, `ConflictResolver`, `config/orchestrator.yaml`), checkpoints, SQLite persistence (`pvs.db`), and `pvs` CLI

### Infrastructure & Security
- ✅ **Docker Sandbox Isolation & Network Egress Filtering**: Isolated container execution (`pvs/agent-sandbox:latest`) running as non-root (`UID 1000`), with dropped Linux capabilities, memory/CPU/PID limits, and zero-trust `iptables` allowlist egress filtering (`NetworkFilter`, `IptablesManager`, `config/sandbox.yaml`) with graceful non-Linux fallback
- ✅ **Policy-Based Authorization**: Cedar policy schema (`policies/schema.cedarschema`) and rules (`policies/agent_authorization.cedar`) with a deterministic Python fallback evaluator
- ✅ **Security Controls & Hybrid ML Secret Detection**: Input/schema validation, path traversal prevention, command sanitization, hybrid regex + ML secret detection (`HybridSecretDetector`, `MLSecretDetector` with Microsoft Presidio + entropy/Base64 feature classifier, YAML allowlists, `<10ms` regex pre-commit hook & `<100ms` hybrid CI scan), and OWASP LLM Top 10 threat model
- ✅ **OpenTelemetry Observability, GenAI Conventions (v1.28+), Monitoring & SLOs**: Standard `GenAITracer` (`chat`, `completion`, `embedding`, `rerank`, `gen_ai.content.*` / `gen_ai.tool.*` events), `GenAIMetrics`, legacy `llm.*` migration shim (`CompatibilityTracer`), OTEL Collector pipeline (`monitoring/otel-collector-config.yml`), 12 production metrics (`PVSMetrics`), 8+ traditional & AI-specific SLIs/SLOs, automated rollback (`ProductionMonitor`), `/health`, `/ready`, `/metrics`, `/slos` endpoints, 8 multi-window burn-rate Prometheus alert rules, Alertmanager routing, and 5 Grafana dashboards including `genai_overview.json` (`docker-compose.monitoring.yml`)
- ✅ **Progressive Delivery & Feature Flags**: Schema-validated YAML feature flags (`flags.yaml`), consistent SHA-256 user bucketing, targeting rules (`user_id`, `environment`, `attribute`, `percentage`), 6-stage `RolloutController` (`dark` → `canary` → `staged_5/25/50` → `full`) with SLO quality gates & automatic rollback, FastAPI Admin UI (`/admin/flags`), orchestrator gating, and `pvs flags` CLI
- ✅ **Experiment Design & A/B Testing**: `ExperimentDesignAgent` turning outcome hypotheses into schema-validated `ExperimentDesign` blueprints, `PowerAnalyzer` (`scipy.stats` sample size & duration), privacy-safe `ABTest` framework with consistent SHA-256 assignment, Welch's t-test / chi-square / Bayesian posterior analysis, guardrail monitoring, early stopping, and `pvs experiment` CLI
- ✅ **Automated Baseline Validation**: Pre-experiment `BaselineCollector` (traces, logs, JSON/YAML), `StatisticalValidator` (sample size, CV, Z-score outliers, Shapiro-Wilk/KS normality), non-central `t` `PowerAnalyzer`, Mann-Whitney U `StabilityChecker` (drift, incidents, deploys), 0–100 quality score `ValidationGate`, `ValidatedExperimentRunner`, and `pvs experiment validate-baseline` CLI
- ✅ **Automated Drift Detection**: AST-based `CodeScanner` (functions + FastAPI routes), Markdown/OpenAPI/SLA `SpecScanner`, OpenTelemetry `TraceScanner`, structural + LLM semantic `DriftComparator`, JSON/Markdown/weekly `DriftReporter`, brownfield baseline diffing, pre-commit hook (`scripts/pre-commit-drift-check.py`), and `pvs drift` CLI
- ✅ **Brownfield Git History & Temporal Analysis**: Read-only `GitAnalyzer` ("Your Code as a Crime Scene"), `HotspotDetector` (high-churn files), `CouplingAnalyzer` (temporal co-change coupling), `AgeAnalyzer` (volatile vs. stable legacy), `OwnershipAnalyzer` (anonymized bus-factor & knowledge silos), `BrownfieldAgent` hotspot × cyclomatic complexity prioritization, and `config/brownfield.yaml`
- ✅ **Architecture Decision Records (ADR) Management**: Git-backed `ADRManager` with auto-numbering (`ADR-0001`), YAML metadata, lifecycle transitions (`proposed` → `accepted` → `deprecated`/`superseded`), keyword search, Mermaid supersession graphs, onboarding decision logs, `# See ADR-XXXX` code scanning, `DecisionAgentADRIntegration`, and `pvs adr` CLI
- ✅ **Spec-Kit Structured Specification Patterns**: 5 reusable Spec-Kit templates (`user-story`, `scenario` Gherkin, `api-contract`, `nfr`, `decision`) with custom template overrides, 0–100 `SpecValidator`, `TraceabilityLinker` (code & test coverage), `DiscoverySpecGenerator` (insights → user stories), VS Code snippets (`.vscode/spec-kit.code-snippets`), and `pvs spec` CLI
- ✅ **Customer Interview Synthesis**: Automated WebVTT/SRT/plain-text `TranscriptProcessor`, multi-layer `PIIRedactor` (emails, phones, CPFs, cards, addresses, names), LLM + offline `InsightExtractor` (severity-rated pain points, JTBD, traceable quotes, human review workflow), cosine-DBSCAN `ThemeClusterer`, `OpportunityMap` integration, JSON/Markdown/Notion exports, `SynthesisDashboard`, and `pvs interview` CLI
- ✅ **Multi-Source Evidence Synthesis Agent**: `EvidenceCollector` (interviews, JSON/CSV support tickets, analytics, competitor research, URLs), `QualityScorer` (recency decay, log sample size, Primary/Secondary/Tertiary hierarchy), semantic `ContradictionDetector` with human-review flags, weighted `SynthesisEngine` with provenance & agreement scores, Mermaid + interactive HTML/SVG evidence networks, and `pvs evidence` CLI

### Model Support & Streaming
- ✅ **OpenAI Adapter**: GPT-4, GPT-4 Turbo, GPT-4o, GPT-3.5 Turbo with real SSE streaming, retries, tool calling, optional semantic `PromptCache`, and USD cost calculation
- ✅ **Anthropic Adapter**: Claude 3.5 Sonnet, Claude 3 Opus, Claude 3 Haiku with real typed SSE streaming, retries, tool-format translation, output secret redaction, optional semantic `PromptCache`, and USD cost calculation
- ✅ **LLM Token & Semantic Prompt Caching**: Hybrid exact (`xxhash64`) + semantic similarity caching (`PromptCache`, `SimilarityMatcher`) with `MemoryCacheBackend` (LRU), `SQLiteCacheBackend`, `RedisCacheBackend`, `TieredCacheBackend`, TTL expiration, model invalidation, and `config/caching.yaml`
- ✅ **Complexity-Based Model Routing**: `ComplexityClassifier` (6-feature scoring with `tiktoken`), `RoutingPolicy` & `ModelRouter` (LOW/MEDIUM/HIGH tier routing, user overrides, shadow mode, hot-reload, FinOps savings metrics), `QualityValidator` (JSON Schema & LLM-judge), `FallbackHandler`, `RoutedModelAdapter`, `Harness` integration, and `config/routing.yaml`
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
- **Multi-source discovery & evidence synthesis**: Customer interview synthesis (`src/product_value_system/discovery/`, EVO-022) and multi-source evidence synthesis (`src/product_value_system/evidence/`, EVO-023) triangulate interviews, support tickets, analytics, competitor research, and URLs with contradiction detection and quality weighting; automated real-time ingestion pipelines from live SaaS tools (Zendesk/Amplitude/Notion APIs) remain future enhancements.

### Lifecycle Stages
- **External analytics required for live Learn stage**: Post-release outcome measurement (`src/product_value_system/learning/`) supports SQLite, PostgreSQL/MySQL (SQLAlchemy), Prometheus, and Mock adapters; live measurement requires an external metrics database or Prometheus instance.
- **Python-focused drift detection**: Automated code/spec/trace drift detection (`src/product_value_system/drift/`, EVO-017) currently parses Python ASTs, Markdown specs, OpenAPI 3.x, and OpenTelemetry trace JSON/JSONL; multi-language AST support (TypeScript/Go) is a future enhancement.

### Evaluation & Testing
- **Trajectory & golden dataset evaluation**: 180 curated golden dataset samples (`golden_datasets/`), 25 exemplar golden trajectories (`golden_datasets/trajectories/`), `TrajectoryCapture`/`TrajectoryEvaluator`, and automated regression evaluation gates (`pvs eval --with-trajectories`, `.github/workflows/eval-gate.yml`) are implemented; real-time trajectory visualization and replay remain future enhancements.
- **Real integration suite requires explicit opt-in**: Standard CI runs fast mocked integration tests (`tests/integration/`); live LLM/Docker/Cedar tests (`tests/integration_real/`) run on-demand or nightly with `RUN_REAL_INTEGRATION_TESTS=1`.

### Security & Deployment Scale
- **Single-machine deployment**: Uses local SQLite (`pvs.db`) and Docker containers (not microVMs such as Firecracker/gVisor, nor Kubernetes).
- **Hybrid ML secret detection**: Secret scanning (`src/product_value_system/security/`, EVO-024) combines a `<10ms` regex fast path with `MLSecretDetector` (Microsoft Presidio + spaCy when `.[security-ml]` is installed, with an automatic entropy/Base64/context ML classifier fallback). Cedar policy / Python fallback synchronization is verified automatically via `scripts/validate-policy-sync.py` and CI (`.github/workflows/policy-validation.yml`).

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
- **[SLO & Monitoring Guides](docs/slo-guide.md)** — Production SLIs/SLOs, [OpenTelemetry GenAI Conventions](docs/otel-genai-conventions.md), [Grafana Dashboards](docs/dashboards.md), [Alerting Runbook](docs/runbooks/alerting.md), and [Monitoring Operations](docs/operations/monitoring.md)
- **[Progressive Delivery & Feature Flags](docs/progressive-delivery.md)** — Dark launches, canary/staged rollouts, [Flag Management](docs/flag-management.md), and [Developer Flag Integration](docs/developer/flag-integration.md)
- **[Experiment Design & A/B Testing](docs/experiment-design.md)** — Power analysis, guardrails, stopping criteria, [A/B Testing Best Practices](docs/ab-testing.md), and [Baseline Validation](docs/baseline-validation.md)
- **[Automated Drift Detection](docs/drift-detection.md)** — Code/spec AST diffing, OpenAPI & OpenTelemetry trace checks, pre-commit gating, and `pvs drift` CLI
- **[Brownfield Git History Analysis](docs/git-history-analysis.md)** — Change hotspots, temporal coupling, code age, anonymized ownership/bus factor, `BrownfieldAgent` hotspot × complexity prioritization, and `config/brownfield.yaml`
- **[Architecture Decision Records (ADR)](docs/adr-guide.md)** — ADR lifecycle, [ADR Index](docs/adr/README.md), decision graphs, onboarding logs, and `pvs adr` CLI
- **[Spec-Kit Patterns Guide](docs/spec-kit-guide.md)** — User story, Gherkin scenario, API contract, NFR & decision templates, validation scoring, traceability, and `pvs spec` CLI
- **[Customer Interview Synthesis Guide](docs/interview-synthesis.md)** — VTT/SRT/plain transcript parsing, PII redaction, JTBD & pain point extraction, theme clustering, Opportunity Map, Notion export, and `pvs interview` CLI
- **[Evidence Synthesis Guide](docs/evidence-synthesis.md)** — Multi-source triangulation (interviews, tickets, analytics, research), quality weighting, contradiction detection, HTML/Mermaid evidence networks, and `pvs evidence` CLI
- **[Threat Model](docs/THREAT_MODEL.md)** — Security analysis (OWASP LLM Top 10)
- **[Security Testing Guide](docs/SECURITY_TESTING.md)** — Adversarial prompt injection & security tests
- **[Policy Synchronization](docs/POLICY_SYNC.md)** — Cedar policy vs. Python fallback sync validation
- **[Cost Management](docs/COST_MANAGEMENT.md)** — Per-run/daily/monthly budget guards and emergency stop
- **[LLM Token Caching Guide](docs/token-caching.md)** — Exact & semantic prompt caching, Memory/SQLite/Redis/Tiered backends, TTL, model invalidation, and `config/caching.yaml`
- **[Model Routing Guide](docs/model-routing.md)** — Complexity classification, cost-sorted model tiers, quality validation & automatic fallback, shadow/rollback modes, and `config/routing.yaml`
- **[Batch Evaluations Guide](docs/batch-evaluations.md)** — OpenAI (50% discount) & Anthropic (30% discount) Batch APIs, hybrid real-time/batch `EvalRunner`, polling, automatic fallback, and `config/evaluation.yaml`
- **[Parallel Agent Execution Guide](docs/parallel-execution.md)** — NetworkX DAG scheduling, `ParallelExecutor`, `ResourceManager` concurrency/memory/RPM guards, `ConflictResolver`, Mermaid visualization, and `config/orchestrator.yaml`
- **[Sandbox Network Egress Filtering Guide](docs/network-filtering.md)** — Zero-trust `iptables` allowlist enforcement, `NetworkFilter`, `IptablesManager`, `DOCKER-USER` hooks, graceful non-Linux fallback, and `config/sandbox.yaml`
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
- ✅ Automated baseline validation & statistical power gating (EVO-019)
- ✅ Customer interview synthesis, PII redaction, theme clustering & Opportunity Map (EVO-022)
- ✅ Multi-source evidence synthesis, contradiction detection, quality weighting & network graph (EVO-023)

### v0.4 — Lifecycle Completion
- ✅ Learn stage & outcome measurement agent (EVO-010)
- ✅ Production SLOs, Prometheus/Grafana monitoring & automated rollback (EVO-014)
- ✅ Progressive delivery, feature flags, Admin UI & SLO-driven rollback (EVO-015)
- ✅ Experiment design agent, power analysis & A/B testing framework (EVO-016)

### v0.5 — Evaluation & Continuous Quality
- ✅ Curated golden dataset evaluation suite (EVO-011)
- ✅ Multi-turn trajectory evaluations (EVO-012)
- ✅ Multi-agent independent review & conflict synthesis (EVO-013)
- ✅ Automated code, specification & OpenTelemetry trace drift detection (EVO-017)
- ✅ Architecture Decision Records (ADR) management & decision agent integration (EVO-018)
- ✅ Spec-Kit structured templates, completeness validator & traceability (EVO-020)
- ✅ OpenTelemetry GenAI semantic conventions (v1.28+), metrics & migration shim (EVO-021)

### v1.0 — Production Grade
- ✅ LLM token & semantic prompt caching (`Memory`/`SQLite`/`Redis`/`Tiered`, EVO-025)
- ✅ Complexity-based model routing, quality validation & automatic fallback (EVO-026)
- ✅ Discounted batch evaluations for OpenAI & Anthropic Batch APIs (EVO-027)
- ✅ DAG-based parallel agent execution, resource throttling & conflict resolution (EVO-028)
- ✅ Zero-trust sandbox network egress filtering via `iptables` allowlists (EVO-029)
- ✅ Brownfield Git history analysis — hotspots, temporal coupling, code age & ownership (EVO-030)
- ✅ Hybrid ML + regex secret detection (Microsoft Presidio + entropy/Base64 classifier, EVO-024)
- 📋 Google Gemini, Azure OpenAI, and local (Ollama/vLLM) model adapters
- 📋 Brownfield Strangler Fig modernization (EVO-031)

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
