# Reference Documentation

This is the **reference documentation** for the AI-native Product Value System. Use this for looking up API signatures, configuration options, schemas, and CLI commands.

**For learning**: See [How-To Guides](../howto/) and [Architecture](../architecture/DIAGRAMS.md).

---

## API Reference

Complete API documentation for all public interfaces.

- [Agents API](api/agents.md) — Agent protocol and implementations (`DiscoveryAgent`, `SpecificationAgent`, `ArchitectureReviewAgent`, `SecurityReviewAgent`, `BuildAgent`)
- [Harness API](api/harness.md) — Harness system components (`Harness`, `ExecutionContext`, `Budget`, `Guardrails`, `Memory`, `ToolRegistry`, `CheckpointManager`)
- [Policy API](api/policy.md) — Policy engine and Cedar integration (`PolicyEngine`, `AuthorizationDecision`, `Agent`, `AgentRole`, `Tool`, `AuthorizationContext`)
- [Evaluation API](api/evaluation.md) — Judges and evaluation pipeline (`Judge`, `EvaluationPipeline`, `SchemaValidationJudge`, `PythonSyntaxJudge`, `TestCoverageJudge`, `CompletenessJudge`, `ClarityJudge`, `GoldenDatasetLoader`)
- [Orchestrator API](api/orchestrator.md) — Value stream orchestration (`ValueStreamOrchestrator`, `ValueStreamStateMachine`, `ValueStreamRun`, `CheckpointManager`)
- [Observability API](api/observability.md) — Tracing, GenAI conventions, cost tracking, and sanitization (`configure_tracing`, `traced`, `CostTracker`, `instrument_model_call`, `SanitizingSpanProcessor`)
- [Simulation API](api/simulation.md) — Synthetic customer simulation (`TinyTroupeAdapter`, `PersonaFactory`, `PopulationGenerator`, `SimulationValidator`, `ScenarioRunner`)
- [Learning & Outcome Measurement API](api/learning.md) — Learn stage analytics adapters, statistical outcome tracking, cost attribution, and Discovery feedback loops (`OutcomeTracker`, `MeasurementAgent`, `CostAttributor`, `FeedbackLoop`, `SQLAnalyticsAdapter`, `PrometheusAnalyticsAdapter`)

---

## Configuration Reference

Configuration options for agents, policies, and system settings.

- [Agent Configurations](configuration/agent-configs.md) — Agent YAML configs (`config/agents/*.yaml`)
- [Policies](configuration/policies.md) — Cedar policy syntax and schema (`policies/`)
- [Environment Variables](configuration/environment-variables.md) — System environment variables

---

## Schema Reference

JSON Schema 2020-12 definitions for all artifacts.

- [Problem](schemas/problem.md) — Problem statement schema (`schemas/problem.schema.json`)
- [Outcome](schemas/outcome.md) — Outcome hypothesis schema (`schemas/outcome.schema.json`)
- [Specification](schemas/specification.md) — Specification schema (`schemas/specification.schema.json`)
- [Evidence](schemas/evidence.md) — Evidence artifact schema (`schemas/evidence.schema.json`)
- [Agent Definition](schemas/agent-definition.md) — Agent config schema (`schemas/agent_definition.schema.json`)

---

## CLI Reference

Command-line interface documentation.

- [Commands](cli/commands.md) — All `pvs` CLI commands and options

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1.0 | 2026-11-18 | Initial release (MVP) |

---

**Last Updated**: 2026-11-18
