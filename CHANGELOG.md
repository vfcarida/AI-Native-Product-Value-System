# Changelog

All notable changes to the **AI-Native Product Value System** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
