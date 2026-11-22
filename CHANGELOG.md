# Changelog

All notable changes to the **AI-Native Product Value System** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-11-22

### Added

- **v1.0 Multi-Provider Adapters, Polyglot Drift, Rust Cedar, Async PostgreSQL & Kubernetes Stack**:
  - Added native `GeminiModelAdapter` (`x-goog-api-key`, `systemInstruction`, tool calling, SSE streaming), `AzureOpenAIModelAdapter` (deployment URL, `api-key` + Entra ID bearer auth, SSE streaming), `OllamaModelAdapter` (`/api/chat`, NDJSON streaming, tool calling, `$0` local cost), unified `create_adapter()` factory, CLI `--provider`/`--model` support on `pvs run`/`eval`/`review`, and multi-provider entries in `config/routing.yaml`.
  - Added `GeminiBatchAdapter` and `AzureOpenAIBatchAdapter` (`50%` batch discount) to `BatchEvalRunner` with JSONL parsing for Gemini `candidates`/`usageMetadata`.
  - Added linear-time structural `TypeScriptScanner` and `GoScanner` (`src/product_value_system/drift/multilang_scanner.py`) extracting TS/JS functions, arrows, class methods, Express/Fastify/NestJS routes, and Go functions (including grouped param types & receivers), `net/http` (Go 1.22+ method prefixes), Gin, Echo, and Chi routes — integrated seamlessly into `CodeScanner`, `SpecScanner`, and `scan_drift()`.
  - Added in-process Rust `cedarpy` PyO3 binding support to `PolicyEngine` (precedence: `cedarpy_rust` -> `cedar_cli` -> `python_fallback`), async PostgreSQL persistence (`PostgresDatabase`, `PostgresValueStreamRepository`, `create_database()`, `migrations/postgres/001_initial_schema.sql`), deterministic lockfile tooling (`scripts/generate-lockfile.py`, `requirements.lock`), and CI `helm-and-supply-chain` job (`helm lint`, `kubeconform`, `pip-audit`).
  - Added production-hardened Kubernetes Helm v3 chart (`deploy/helm/product-value-system/`) with JSON Schema 2020-12 `values.schema.json`, Restricted PSS non-root UID 10001 security context, `/health/live` & `/health/ready` probes, zero-trust egress `NetworkPolicy`, `HorizontalPodAutoscaler`, `PodDisruptionBudget`, Prometheus `ServiceMonitor`, optional companion OpenTelemetry Collector (`otel-collector.yaml`), and Grafana dashboard ConfigMap (`grafana-dashboards-configmap.yaml`).
  - Eliminated SciPy `ks_2samp` tie `RuntimeWarning` in `SimulationValidator` (0 warnings across the full test suite).
- **Evolution Package P0–P2 (`EVO-001`–`EVO-033`)**:
  - Anthropic Claude adapter & real SSE streaming (`EVO-001`, `EVO-002`), live integration test tier & cost guards (`EVO-003`, `EVO-007`, `EVO-008`), Cedar/Python 100% policy parity validator (`EVO-005`), adversarial prompt injection suite (`EVO-006`).
  - TinyTroupe synthetic customer simulation (`EVO-009`), post-launch Learn stage & outcome feedback loops (`EVO-010`), 180-sample golden datasets + trajectory evaluations (`EVO-011`, `EVO-012`), 4-perspective multi-agent review (`EVO-013`), SLOs & Prometheus/Grafana monitoring (`EVO-014`), progressive feature flags & Admin UI (`EVO-015`), A/B experiment design & baseline validation (`EVO-016`, `EVO-019`).
  - Spec/code/trace drift detection (`EVO-017`), ADR lifecycle manager (`EVO-018`), Spec-Kit templates (`EVO-020`), OpenTelemetry GenAI v1.28+ conventions (`EVO-021`), customer interview synthesis (`EVO-022`), multi-source evidence synthesis (`EVO-023`), and Hybrid ML + Regex secret detection (`EVO-024`).
  - FinOps & scale capabilities: Tiered L1/L2/Redis semantic prompt caching (`EVO-025`), 6-feature complexity model routing (`EVO-026`), OpenAI/Anthropic batch evaluation runner (`EVO-027`), NetworkX DAG parallel agent orchestrator (`EVO-028`), Docker `iptables` egress allowlist filtering (`EVO-029`), Git history forensics (`EVO-030`), Strangler Fig modernization & compilable ACL generation (`EVO-031`), privacy-preserving SPACE/DORA DevX telemetry (`EVO-032`), and self-documenting `Makefile` + `.env.example` (`EVO-033`).
- **20-Round Production Hardening & Research Alignment**:
  - Strict Pydantic v2 `StrictConfigModel` (`extra="forbid"`, `frozen=True`) validating 100% (`12/12`) of `config/*.yaml` files with safe `${VAR:-default}` and OWASP `*_FILE` secret-file interpolation.
  - AWS Builders' Library resilience primitives (`CircuitBreaker`, `RetryTokenBucket`, `full_jitter_delay`, `execute_with_resilience`) in `harness/resilience.py`.
  - Clauset-Newman-Moore greedy modularity community detection + Newman-Girvan $Q$ metric in `BoundaryAnalyzer` and GitHub Scientist-style shadow-mode parity validation + declarative field mapping in `ACLGenerator` (eliminating all `TODO` stubs in `src/`).
  - Unified `/health/live`, `/health/ready`, `/metrics` (`pvs_*` + `devx_*`), and aggregate-only `/devx` endpoints; PEP 562 lazy top-level SDK exports.
  - OpenTelemetry-correlated `StructuredJsonFormatter`, handler-attached `SecretRedactingFilter`, `configure_logging()`, and `run_system_diagnostics()`.
  - Modular CLI subcommands `pvs doctor`, `pvs route`, `pvs brownfield --strangler`, and `pvs devx` with `--json` output and semantic exit codes.
  - Reproducible `$0` empirical benchmark harness (`scripts/run_benchmarks.py`) & measured report (`docs/BENCHMARKS.md`), 3 zero-cost showcase demos (`examples/showcases/`), formal ADRs `ADR-0004`–`ADR-0006`, Hypothesis property-based & stateful invariant tests, and full-stack E2E golden-path test.

### Changed

- Reduced McCabe cyclomatic complexity of `regression_eval._score_criterion` (`73 -> 2`) and `controller._execute_stage` (`43 -> 5`) via O(1) dispatch tables; replaced library `print()` and silent `except: pass` blocks with structured `logging`.
- Eliminated per-call `cedar` CLI subprocess probing when `cedar` is absent from `PATH` (`PolicyEngine.authorize`: `377` -> `163,840 eval/s`, `32.1x` warm init) and removed double write-amplification in `SQLiteCacheBackend`/`PromptCache.get()` via a persistent WAL connection + deferred LRU touch flush (`28.5 ms` -> `0.276 ms` p50, `103x` faster).
- Aligned OpenTelemetry GenAI tracing with OTel GenAI Agents SIG v1.29–v1.30+ (`invoke_agent`, `execute_tool`, `gen_ai.response.finish_reasons` array, and `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` opt-out privacy support).
- Added `__main__.py` and PEP 562 lazy `cli/__init__.py` exports for zero-warning `python -W error -m product_value_system` execution; exposed `CircuitBreaker`, `validate_all_configs`, and `run_system_diagnostics` from top-level `product_value_system`; reconciled `requirements.txt` with `pyproject.toml`.
- Upgraded `Dockerfile` to a hardened multi-stage non-root (`UID 10001`) build with `HEALTHCHECK`, modernized `scripts/final-audit.sh`, `.pre-commit-config.yaml` (6 local gates), `.github/workflows/ci.yml` (`concurrency`, pip cache, `security-and-governance` job), and `README.md` (including candid Known Limitations & Non-Goals).

### Fixed

- Fixed fabricated `SPACE` (`0.49` -> `0.00`) and `DORA` (`medium` -> `insufficient_data`) scores reported on empty trackers with zero observations; added explicit non-finite (`NaN`/`Inf`) rejection in `StatisticalValidator` and `PowerAnalyzer`.
- Fixed `full_jitter_delay` `OverflowError` on large attempt counts, `CircuitBreaker`/`RetryTokenBucket` `NaN` deadlock/starvation, and non-standard `NaN`/`Infinity` JSON keys in `PromptCache.compute_cache_key`.
- Hardened both `CheckpointManager` implementations with atomic `mkstemp + fsync + os.replace` writes and `.json.corrupt` quarantine recovery, plus `PRAGMA quick_check` `.db.corrupt` quarantine-and-rebuild recovery in `SQLiteCacheBackend`.
- Fixed 6 misnamed class symbols and 4 broken CLI invocations across documentation, unified repository GitHub URLs, and added automated executable-docs regression testing (`tests/unit/test_docs_truth.py`).

### Removed

- Deleted dead empty package `src/product_value_system/_internal/`, orphan root `golden_dataset/` duplicate, 5 obsolete `.gitkeep` scaffold files, and duplicated HTTP auth/security-header middleware across `api/health.py` and `feature_flags/admin_ui.py`.

### Security

- Fixed fail-open IEEE 754 `NaN`/`-inf`/negative `cost_so_far` and negative `iteration_count` policy budget bypasses in `PolicyEngine._fallback_evaluate`.
- Eliminated a `4,004 ms` CWE-1333 ReDoS catastrophic backtracking regex in `drift/spec_scanner.py` (`< 1 ms` linear time).
- Fixed CWE-94 code-generation injection (`module.name` sanitized + `repr`-quoted) and `repo_path` traversal in `brownfield/acl_generator.py`, plus path-traversal guards on checkpoint IDs.
- Fixed API-key timing side-channel in `api/health.py` and `feature_flags/admin_ui.py` using constant-time `hmac.compare_digest` and verified HTML XSS escaping on admin views.
- Enforced OWASP HTTP security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cache-Control`, `Content-Security-Policy`) across all FastAPI endpoints.
- Added automated AST & HTTP security posture audit (`tests/security/test_security_posture_audit.py`) verifying zero `shell=True`, `yaml.load`, `eval`, `exec`, or `pickle.load` in `src/`.

## [0.1.0] - 2026-11-18

### Added

- **Contracts & JSON Schemas (`P002`)**: JSON Schema 2020-12 definitions and `SchemaValidator` for `problem`, `outcome`, `specification`, `evidence`, `agent_definition`, and `event_envelope`.
- **Model Adapter Interface (`P003`)**: `@runtime_checkable` `ModelAdapter` protocol with `OpenAIModelAdapter` and deterministic `MockModelAdapter`.
- **Cedar Policy Engine (`P004`)**: Declarative Cedar schema (`policies/schema.cedarschema`), 8 authorization policies (`policies/agent_authorization.cedar`), and `PolicyEngine` with audit logging.
- **Docker Sandbox Provider (`P005`)**: Isolated non-root container execution (`DockerSandboxProvider`) with CPU/memory/network/filesystem quotas.
- **OpenTelemetry Observability (`P006`, `P021`)**: GenAI semantic convention spans, `CostTracker`, `SanitizingSpanProcessor`, and secret redaction.
- **Agent Harness (`P007`)**: `Harness`, `Budget`, `ExecutionContext`, `ToolRegistry`, `Memory`, `Guardrails`, and `CheckpointManager`.
- **Modular Agents (`P008`–`P012`)**: `BaseAgent`, `AgentRegistry`, `DiscoveryAgent`, `SpecificationAgent`, `ArchitectureReviewAgent`, `SecurityReviewAgent`, and `BuildAgent`.
- **Evaluation Framework (`P013`)**: Deterministic judges (`SchemaValidationJudge`, `TestExecutionJudge`, `TestCoverageJudge`), AI judges (`CompletenessJudge`, `ClarityJudge`), `GoldenDataset`, and `EvaluationPipeline`.
- **Value Stream Orchestrator (`P014`)**: `ValueStreamOrchestrator`, `StateMachine`, and `CheckpointCoordinator`.
- **SQLite Persistence (`P015`)**: Durable SQLite database with schema migrations (`migrations/001_initial_schema.sql`) and `RunRepository`.
- **CLI Interface (`P016`)**: `pvs` CLI supporting `init`, `run`, `status`, `list`, `show`, and `validate`.
- **Greenfield & Brownfield Demos (`P017`, `P018`)**: End-to-end AcmeBank instant payment notification greenfield example and legacy Customer API brownfield drift detection example.
- **Security & Secret Scanning (`P019`–`P021`)**: OWASP LLM Top 10 threat model (`docs/THREAT_MODEL.md`), input/path/command/output sanitization (`product_value_system.security`), and Git pre-commit secret scanning (`scripts/pre-commit-secret-scan.py`).
- **Architecture, Reference, and How-To Documentation (`P022`–`P025`)**: 8 C4/Sequence/Trust/Data-flow Mermaid diagrams, 16 API/Schema/CLI reference guides, and 7 task-oriented How-To guides.
