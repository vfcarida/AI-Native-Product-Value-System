# Reference Documentation

This is the **Diátaxis Reference** quadrant for the AI-Native Product Value System. It provides objective, accurate lookup tables for public Python packages, configuration contracts, JSON schemas, and CLI commands.

**Other Diátaxis quadrants**:
- **Tutorials & Examples**: [`examples/`](../../examples/)
- **How-To Guides**: [`docs/howto/`](../howto/README.md)
- **Architecture & Explanation**: [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md), [`docs/architecture/DIAGRAMS.md`](../architecture/DIAGRAMS.md), [`docs/BENCHMARKS.md`](../BENCHMARKS.md)

---

## Core Python Package Surface (31 Public Packages)

Top-level lazy imports are exposed directly on `product_value_system` (PEP 562 `__getattr__`), e.g. `from product_value_system import ValueStreamOrchestrator, Harness, PolicyEngine, PromptCache, ModelRouter, DevXTracker`.

### Control Plane, Agents & Orchestration
- [`product_value_system.agents`](api/agents.md) — `Agent`, `DiscoveryAgent`, `SpecificationAgent`, `ArchitectureReviewAgent`, `SecurityReviewAgent`, `BuildAgent`, `BrownfieldAgent`, `MeasurementAgent`, `ExperimentDesignAgent`
- [`product_value_system.orchestrator`](api/orchestrator.md) — `ValueStreamOrchestrator`, `DependencyGraph`, `ParallelExecutor`, `ResourceManager`, `ConflictResolver`, `CheckpointManager` ([Parallel Execution Guide](../parallel-execution.md))
- [`product_value_system.harness`](api/harness.md) — `Harness`, `ExecutionContext`, `Budget`, `Guardrails`, `CircuitBreaker`, `RetryTokenBucket`, `execute_with_resilience`
- [`product_value_system.review`](../architecture/DIAGRAMS.md) — `MultiAgentReviewOrchestrator` (parallel Architecture, Security, Performance, Maintainability review synthesis)
- [`product_value_system.adapters`](api/agents.md) — `ModelAdapter`, `OpenAIAdapter`, `AnthropicAdapter`, `MockModelAdapter`, `SQLAnalyticsAdapter`, `PrometheusAnalyticsAdapter`
- `product_value_system.persistence` — WAL-tuned SQLite `Database` & `ValueStreamRepository`

### Governance, Security & Sandboxing
- [`product_value_system.policy`](api/policy.md) — mtime-cached Cedar `PolicyEngine`, `AuthorizationDecision`, `Agent`, `AgentRole`, `Tool`
- `product_value_system.security` — `HybridSecretDetector` (regex + Presidio/ML + entropy), `InputValidator`, `OutputSanitizer`
- `product_value_system.sandbox` / `product_value_system.runtime` — `DockerSandboxProvider`, `NetworkFilter`, `IptablesManager` ([Network Egress Filtering](../network-filtering.md))
- `product_value_system.contracts` — `SchemaValidator`, `StrictConfigModel`, `validate_all_configs`, `expand_env_vars`, `read_secret_from_file`

### FinOps & Performance Optimization
- `product_value_system.caching` — `PromptCache`, `MemoryCacheBackend`, `SQLiteCacheBackend` (WAL), `RedisCacheBackend`, `TieredCacheBackend`, `SimilarityMatcher` ([Token Caching Guide](../token-caching.md))
- `product_value_system.routing` — `ModelRouter`, `ComplexityClassifier`, `RoutingPolicy`, `QualityValidator`, `FallbackHandler` ([Model Routing Guide](../model-routing.md))
- [`product_value_system.evaluation`](api/evaluation.md) — `EvaluationPipeline`, `BatchEvalRunner`, `OpenAIBatchAdapter`, `AnthropicBatchAdapter`, `RegressionEvaluator`, `TrajectoryEvaluator` ([Batch Evaluations Guide](../batch-evaluations.md))

### Discovery, Evidence, Simulation & Learning
- [`product_value_system.simulation`](api/simulation.md) — `TinyTroupeAdapter`, `PersonaFactory`, `PopulationGenerator`, `SimulationValidator`, `ScenarioRunner`
- `product_value_system.discovery` — AI-assisted `InterviewSynthesizer` & customer insight extraction
- `product_value_system.evidence` — `SynthesisEngine`, `ContradictionDetector`, `QualityScorer`
- `product_value_system.experiments` / `product_value_system.validation` — `PowerAnalyzer`, `ExperimentDesignAgent`, `StatisticalValidator`, `ValidationGate`
- [`product_value_system.learning`](api/learning.md) — `OutcomeTracker`, `CostAttributor`, `FeedbackLoop`

### Brownfield Modernization & Engineering Governance
- `product_value_system.brownfield` — `GitAnalyzer`, `HotspotDetector`, `CouplingAnalyzer`, `AgeAnalyzer`, `OwnershipAnalyzer`, `BoundaryAnalyzer` (Clauset-Newman-Moore + $Q$), `ACLGenerator` (shadow parity mode), `RoutingStrategyPlanner`, `StranglerPlanner` ([Git History Analysis](../git-history-analysis.md), [Strangler Fig Modernization](../strangler-fig-modernization.md))
- `product_value_system.drift` — Spec/code drift detection engine
- `product_value_system.adr` — Architecture Decision Record lifecycle manager
- `product_value_system.spec_kit` — Specification templates, traceability linker, and validator
- `product_value_system.feature_flags` — `FeatureFlagService`, `RolloutController`, FastAPI Admin UI

### Observability, Monitoring & DevX
- [`product_value_system.observability`](api/observability.md) — OpenTelemetry GenAI v1.28+ tracing (`GenAITracer`, `SanitizingSpanProcessor`), `StructuredJsonFormatter`, `SecretRedactingFilter`, `configure_logging`, `run_system_diagnostics`
- `product_value_system.monitoring` / `product_value_system.api` — `PVSMetrics`, `SLOEvaluator`, `ProductionMonitor`, FastAPI `/health/live`, `/health/ready`, `/slos`, `/metrics`, `/devx`
- `product_value_system.metrics` — `DevXTracker`, `CycleTimeTracker`, `SPACECalculator`, `SatisfactionSurvey` ([DevX Metrics Guide](../devx-metrics.md))
- [`product_value_system.cli`](cli/commands.md) — Modular `pvs` CLI (21 subcommands)

---

## Configuration Reference (12 Strict YAML Contracts)

All 12 top-level files in `config/` are validated fail-fast by Pydantic v2 `StrictConfigModel` (`extra="forbid"`, `frozen=True`) via `validate_all_configs()` or `pvs doctor`:

| File | Schema Model | Purpose |
|---|---|---|
| `config/orchestrator.yaml` | `OrchestratorConfigFile` | Parallel execution concurrency, resource limits, conflict resolution |
| `config/caching.yaml` | `CachingConfigFile` | L1/L2 prompt cache backend, TTL, semantic similarity threshold |
| `config/routing.yaml` | `RoutingConfigFile` | Complexity tiers, provider pricing, fallback chains, shadow mode |
| `config/evaluation.yaml` | `EvaluationConfigFile` | OpenAI/Anthropic batch API size, polling, realtime fallback |
| `config/sandbox.yaml` | `SandboxConfigFile` | Zero-trust egress allowlist, DNS TTL, iptables fallback |
| `config/brownfield.yaml` | `BrownfieldConfigFile` | Git history lookback, hotspot weights, Strangler Fig rollout stages |
| `config/metrics.yaml` | `MetricsConfigFile` | SPACE dimensions, DORA tracking, privacy (`aggregate_only: true`) |
| `config/budget_limits.yaml` | `BudgetLimitsConfigFile` | Per-run / daily / monthly USD limits & `EMERGENCY_STOP` switch |
| `config/secret_detection.yaml` | `SecretDetectionConfigFile` | Hybrid ML confidence & Shannon entropy thresholds |
| `config/secret_detection_allowlist.yaml` | `SecretDetectionAllowlistConfigFile` | Audited synthetic placeholder allowlist |
| `config/model_adapters.yaml` | `ModelAdaptersConfigFile` | OpenAI / Anthropic / Mock adapter defaults & `${VAR}` bindings |
| `config/analytics_adapters.yaml` | `AnalyticsAdaptersConfigFile` | SQL / Prometheus / Mock post-launch analytics measurement |

Additional configuration guides:
- [Agent Configurations](configuration/agent-configs.md) (`config/agents/*.yaml`)
- [Cedar Policies](configuration/policies.md) (`policies/agent_authorization.cedar`, `policies/schema.cedarschema`)
- [Environment Variables](configuration/environment-variables.md) (`${VAR:-default}` & OWASP `*_FILE` secret mounts)

---

## Schema Reference (JSON Schema 2020-12)

- [Problem](schemas/problem.md) (`schemas/problem.schema.json`)
- [Outcome](schemas/outcome.md) (`schemas/outcome.schema.json`)
- [Specification](schemas/specification.md) (`schemas/specification.schema.json`)
- [Evidence](schemas/evidence.md) (`schemas/evidence.schema.json`)
- [Agent Definition](schemas/agent-definition.md) (`schemas/agent_definition.schema.json`)

---

## CLI Reference

- [All 21 `pvs` Commands & Semantic Exit Codes](cli/commands.md)

---

**Last Updated**: 2026-11-22
