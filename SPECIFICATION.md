# AI-Native Product Value System: Canonical Specification

## 1. Purpose and Vision

### 1.1 What Is This System?

The **AI-native Product Value System** is a reference implementation demonstrating how organizations can integrate artificial intelligence into the complete product development lifecycle—from problem identification through outcome measurement—while maintaining quality, security, and governance.

**This is NOT**:
- A coding copilot or IDE plugin
- A single AI agent that writes code
- A production banking platform
- A vendor product

**This IS**:
- A complete socio-technical system for AI-native product development
- A reference architecture for enterprise AI adoption
- A runnable demonstration of principled AI integration
- A teaching tool for engineering teams and researchers

### 1.2 The Core Thesis

Traditional software development assumes **humans write code, humans write tests, humans make all decisions**. AI changes the economics:

> **AI drastically reduces the marginal cost of producing artifacts** (code, tests, documentation, analyses, prototypes). This shifts bottlenecks from production to **context quality, intent clarity, verification, governance, and organizational learning**.

The system demonstrates how to build quality, security, and accountability into an AI-accelerated development process.

### 1.3 The Value Stream Run

The fundamental unit of the system is a **Value Stream Run**:

```
Problem Statement
    ↓
Outcome Hypothesis
    ↓
Discovery & Evidence
    ↓
Decision Gate
    ↓
Specification
    ↓
Build (with agents)
    ↓
Review (human + agent)
    ↓
Release Gate
    ↓
Run (in environment)
    ↓
Learn (measure outcome)
    └─────→ New Problem / Outcome [feedback loop]
```

Every artifact produced, every agent action taken, and every decision made is traceable within this flow.

## 2. Users and Use Cases

### 2.1 Primary Users

| User Type | Needs | How This System Helps |
|-----------|-------|----------------------|
| **Engineering Leaders** | Understand AI-native development patterns | Demonstrates governance, security, measurement |
| **Product Managers** | See how AI changes discovery and delivery | Shows AI-assisted discovery and outcome tracking |
| **AI Engineers** | Learn agent architecture patterns | Provides reference harness, orchestration, evals |
| **Security Engineers** | Understand AI-specific risks | Demonstrates isolation, policy, audit trails |
| **Enterprise Architects** | Evaluate AI integration strategies | Shows contract-based, modular architecture |
| **Researchers** | Study AI-native development | Provides observable, measurable reference implementation |

### 2.2 Primary Use Cases

**UC-001: Greenfield Product Development**
- Input: Problem statement, target outcome
- Process: Discovery → Specification → Build → Release
- Output: Working software with evidence of outcome

**UC-002: Brownfield System Understanding**
- Input: Existing codebase without documentation
- Process: Bootstrap documentation, identify drift, propose improvements
- Output: Architecture docs, gap analysis, modernization candidates

**UC-003: Policy-Governed Agent Execution**
- Input: Agent attempting tool call
- Process: Policy evaluation, authorization decision
- Output: Allow/deny with audit trail

**UC-004: Multi-Agent Collaboration**
- Input: Specification requiring diverse expertise
- Process: Orchestrator delegates to specialized agents
- Output: Composite artifact from multiple perspectives

**UC-005: Evaluation and Quality Gates**
- Input: Agent-produced artifact
- Process: Deterministic tests + evaluations + policy checks
- Output: Pass/fail decision with evidence

## 3. Requirements

All requirements use stable IDs (REQ-XXX) for traceability.

### 3.1 Functional Requirements: Value Stream Orchestration

**REQ-001**: The system SHALL implement a complete value stream run from problem statement to outcome measurement.

**REQ-002**: The system SHALL produce versionable, machine-readable artifacts at each stage (problem.yaml, outcome.yaml, specification.yaml, etc.).

**REQ-003**: The system SHALL enforce evidence-based progression: no stage transition without evidence artifact.

**REQ-004**: The system SHALL support human override at any decision gate.

**REQ-005**: The system SHALL record all state transitions with timestamp, actor, and rationale.

### 3.2 Functional Requirements: Agent System

**REQ-010**: The system SHALL support modular agent definitions with explicit capabilities, tools, and constraints.

**REQ-011**: Agents SHALL operate within harnesses that control context, tools, memory, and execution.

**REQ-012**: The system SHALL support agent-to-agent delegation with traceability.

**REQ-013**: Agents SHALL NOT share identity with the humans who initiate them.

**REQ-014**: Agent outputs SHALL be validated before execution.

**REQ-015**: The system SHALL implement graduated autonomy levels (L0: advise → L4: supervised autonomy).

### 3.3 Functional Requirements: Discovery and Product

**REQ-020**: The system SHALL accept problem statements and generate outcome hypotheses.

**REQ-021**: Discovery agents SHALL synthesize evidence from multiple sources (synthetic interviews, documents, data).

**REQ-022**: The system SHALL support synthetic customer simulation for scenario exploration.

**REQ-023**: Outcome hypotheses SHALL include measurable success criteria and guardrails.

**REQ-024**: The system SHALL validate hypotheses against baseline evidence before proceeding.

### 3.4 Functional Requirements: Specification and Review

**REQ-030**: Specification agents SHALL produce machine-readable, testable specifications.

**REQ-031**: Review agents SHALL operate in isolated context to avoid confirmation bias.

**REQ-032**: The system SHALL implement spec-driven development (specification precedes implementation).

**REQ-033**: The system SHALL detect drift between specification and implementation.

**REQ-034**: Architecture review SHALL be independent and agent-assisted.

### 3.5 Functional Requirements: Build and Test

**REQ-040**: Build agents SHALL implement specifications in isolated sandbox environments.

**REQ-041**: The system SHALL generate tests automatically based on specifications.

**REQ-042**: All code SHALL pass deterministic tests before proceeding.

**REQ-043**: The system SHALL support brownfield understanding and modernization.

**REQ-044**: Build agents SHALL commit work incrementally with meaningful messages.

### 3.6 Functional Requirements: Quality and Evaluation

**REQ-050**: The system SHALL implement three test tiers: unit (deterministic), integration (probabilistic), eval (trajectory).

**REQ-051**: Evaluations SHALL measure agent behavior, not just output quality.

**REQ-052**: The system SHALL maintain a golden dataset for regression evaluation.

**REQ-053**: Evaluation gates SHALL block releases when thresholds are breached.

**REQ-054**: The system SHALL detect hallucinated dependencies, API calls, and test manipulations.

### 3.7 Functional Requirements: Policy and Authorization

**REQ-060**: Every agent action SHALL require policy authorization before execution.

**REQ-061**: Policies SHALL be defined declaratively in Cedar policy language.

**REQ-062**: Policies SHALL enforce least-privilege access to tools and data.

**REQ-063**: Policy violations SHALL be logged and SHALL NOT silently fail.

**REQ-064**: The system SHALL support policy schemas with validation.

**REQ-065**: Autonomy level SHALL be explicitly declared per agent and action type.

### 3.8 Functional Requirements: Observability and Cost

**REQ-070**: The system SHALL instrument all agent interactions with OpenTelemetry.

**REQ-071**: Traces SHALL include: model, tokens, cost, latency, tools called, outcome.

**REQ-072**: The system SHALL track cost per value stream run and per agent.

**REQ-073**: Cost SHALL be attributed to outcome (cost per successful outcome).

**REQ-074**: The system SHALL provide dashboards for: latency, cost, quality, autonomy rate.

**REQ-075**: Telemetry SHALL NOT leak sensitive data (PII, credentials).

### 3.9 Non-Functional Requirements: Security

**REQ-100**: Agents SHALL execute in isolated containers with minimal capabilities.

**REQ-101**: All LLM inputs SHALL be validated to prevent prompt injection.

**REQ-102**: All LLM outputs SHALL be sanitized before execution.

**REQ-103**: Secrets SHALL NEVER be passed to LLMs or logged in traces.

**REQ-104**: The system SHALL implement OWASP LLM Top 10 controls.

**REQ-105**: Containers SHALL run as non-root users.

**REQ-106**: Network egress SHALL be restricted to approved endpoints.

### 3.10 Non-Functional Requirements: Reliability

**REQ-110**: The system SHALL support checkpoint-based recovery from failures.

**REQ-111**: Long-running workflows SHALL persist state to survive crashes.

**REQ-112**: The system SHALL implement retry with exponential backoff for transient failures.

**REQ-113**: Resource exhaustion SHALL be prevented via budgets (time, cost, iterations).

**REQ-114**: The system SHALL implement kill switches for runaway agents.

### 3.11 Non-Functional Requirements: Usability

**REQ-120**: The system SHALL be runnable on a single machine without cloud dependencies.

**REQ-121**: Installation SHALL require only Docker and Python 3.12+.

**REQ-122**: `make demo` SHALL execute a complete value stream run with synthetic data.

**REQ-123**: All artifacts SHALL be human-readable (YAML, Markdown, JSON).

**REQ-124**: Error messages SHALL be actionable with recovery guidance.

### 3.12 Non-Functional Requirements: Maintainability

**REQ-130**: All public APIs SHALL use JSON Schema contracts.

**REQ-131**: Contracts SHALL be versioned with semantic versioning.

**REQ-132**: The system SHALL separate domain contracts from adapter implementations.

**REQ-133**: Changing model providers SHALL NOT require changing domain logic.

**REQ-134**: The system SHALL maintain architecture decision records (ADRs).

### 3.13 Non-Functional Requirements: Data and Privacy

**REQ-140**: The MVP SHALL use ONLY synthetic data (no real customer data).

**REQ-141**: The system SHALL support data classification and policy enforcement.

**REQ-142**: PII detection SHALL flag potentially sensitive data in outputs.

**REQ-143**: All data SHALL be deletable (support for right to erasure).

**REQ-144**: Telemetry SHALL support regional data residency requirements.

### 3.14 Non-Functional Requirements: Performance

**REQ-150**: A single value stream run SHALL complete in < 15 minutes (MVP, synthetic workload).

**REQ-151**: Unit tests SHALL execute in < 2 minutes.

**REQ-152**: Evaluation gates SHALL complete in < 5 minutes.

**REQ-153**: The system SHALL support parallel agent execution where independent.

**REQ-154**: Token caching SHALL reduce redundant LLM calls.

## 4. Non-Goals (Explicit Exclusions)

**NG-001**: This is NOT a production banking platform. No live customer data, no real transactions.

**NG-002**: This is NOT a multi-tenancy SaaS. Single-user, local execution only.

**NG-003**: This is NOT a replacement for human judgment. Humans remain accountable for outcomes.

**NG-004**: This is NOT a "one agent to rule them all". Specialization is deliberate.

**NG-005**: This is NOT a model training system. It uses pre-trained foundation models.

**NG-006**: This is NOT a general-purpose orchestration platform. It is domain-specific.

**NG-007**: This is NOT optimized for maximum speed. Quality and safety take precedence.

**NG-008**: This is NOT a knowledge graph system. Context is document-based (MVP).

**NG-009**: This is NOT a replacement for DORA metrics. It complements them.

**NG-010**: This does NOT implement all 7 layers of the Product Value System fully. It demonstrates principles.

## 5. Terminology

| Term | Definition |
|------|------------|
| **Value Stream Run** | Complete execution from problem → outcome measurement |
| **Agent** | Goal-oriented entity combining model + tools + state |
| **Harness** | System around agent controlling context, tools, permissions |
| **Orchestrator** | Coordinates multiple agents and workflows |
| **Runtime** | Execution environment (container, VM, sandbox) |
| **Policy** | Declarative rules authorizing agent actions |
| **Eval** | Evaluation measuring agent behavior or output quality |
| **Golden Dataset** | Curated examples for regression evaluation |
| **Trajectory** | Sequence of agent reasoning steps and tool calls |
| **Evidence** | Artifact proving a claim or justifying a decision |
| **Outcome Hypothesis** | Testable prediction about product impact |
| **Autonomy Level** | Degree of independence granted to an agent (L0-L4) |
| **Evidence Gate** | Decision point requiring evidence to proceed |
| **Synthetic Customer** | Simulated user for scenario exploration |
| **Drift** | Divergence between specification and implementation |

## 6. Constraints

### 6.1 Technical Constraints

**CON-001**: Must run on macOS, Linux, Windows (via WSL2)

**CON-002**: Requires Docker Desktop or Docker Engine

**CON-003**: Requires Python 3.12 or higher

**CON-004**: Requires 8GB RAM minimum, 16GB recommended

**CON-005**: Requires 20GB disk space for images and artifacts

**CON-006**: Internet access required for model API calls (or local model server)

### 6.2 Operational Constraints

**CON-010**: MVP demonstrates principles; production requires additional hardening

**CON-011**: No official support; community-driven maintenance

**CON-012**: Performance not optimized for large-scale workloads

**CON-013**: Single-machine deployment only (no distributed execution)

### 6.3 Regulatory Constraints (for context)

The source documents reference Brazilian financial regulation (CMN 4.893, LGPD). While this reference implementation does NOT process real financial data, the architecture demonstrates patterns relevant to regulated environments:

- Auditability (all actions traced)
- Policy enforcement (authorization required)
- Data classification (PII detection)
- Human oversight (approval gates)
- Security controls (isolation, least privilege)

## 7. Architecture Overview

(See ARCHITECTURE.md for complete design.)

### 7.1 Layered Architecture

```
┌─────────────────────────────────────────────┐
│   Value Stream Orchestrator                 │
├─────────────────────────────────────────────┤
│   Agents (Discovery, Spec, Build, Review)   │
├─────────────────────────────────────────────┤
│   Harnesses (Context, Tools, Memory)        │
├─────────────────────────────────────────────┤
│   Policy Engine (Cedar Authorization)       │
├─────────────────────────────────────────────┤
│   Runtime (Docker Sandbox)                  │
├─────────────────────────────────────────────┤
│   Adapters (Models, Knowledge, Tools)       │
├─────────────────────────────────────────────┤
│   Observability (OpenTelemetry)             │
└─────────────────────────────────────────────┘
```

### 7.2 Repository Structure

```
ai-native-product-value-system/
├── src/
│   └── product_value_system/
│       ├── contracts/         # JSON Schemas
│       ├── orchestrator/      # Value stream controller
│       ├── agents/            # Agent implementations
│       ├── harness/           # Harness components
│       ├── policy/            # Cedar policies
│       ├── evaluation/        # Eval framework
│       ├── adapters/          # Model/tool adapters
│       └── observability/     # OpenTelemetry
├── tests/
│   ├── unit/
│   ├── integration/
│   └── eval/
├── examples/
│   ├── greenfield/
│   └── brownfield/
├── policies/
├── schemas/
└── docs/
```

## 8. External Interfaces

### 8.1 Model Provider Interface

```python
class ModelAdapter(Protocol):
    async def invoke(
        self,
        messages: List[Message],
        tools: Optional[List[Tool]],
        **kwargs
    ) -> ModelResponse:
        ...
```

Adapters for: OpenAI, Anthropic, Google, Azure, local models.

### 8.2 Policy Engine Interface

```python
class PolicyEngine(Protocol):
    def authorize(
        self,
        principal: Agent,
        action: Action,
        resource: Resource,
        context: Dict
    ) -> AuthorizationDecision:
        ...
```

Implementation: Cedar policy engine.

### 8.3 Sandbox Interface

```python
class SandboxProvider(Protocol):
    def create(self, config: SandboxConfig) -> Sandbox:
        ...
    
    def exec(self, sandbox: Sandbox, command: str) -> Result:
        ...
    
    def destroy(self, sandbox: Sandbox) -> None:
        ...
```

MVP: Docker. Future: E2B, Kubernetes Jobs, microVMs.

### 8.4 Knowledge Interface

```python
class KnowledgeAdapter(Protocol):
    def search(self, query: str, filters: Dict) -> List[Document]:
        ...
    
    def retrieve(self, id: str) -> Document:
        ...
```

MVP: Git + filesystem. Future: RAG, knowledge graph.

## 9. Data Lifecycle

### 9.1 Artifact Types

| Artifact | Format | Persistence | Version Control |
|----------|--------|-------------|-----------------|
| Problem Statement | YAML | SQLite + Git | Yes |
| Outcome Hypothesis | YAML | SQLite + Git | Yes |
| Discovery Evidence | JSON | SQLite + Git | Yes |
| Specification | Markdown + YAML | Git | Yes |
| Code | Language-specific | Git | Yes |
| Tests | Language-specific | Git | Yes |
| Evaluation Results | JSON | SQLite + filesystem | No (ephemeral) |
| Traces | OTLP | Local collector | No (retention limit) |
| Policy Decisions | JSON | SQLite | No (auditable) |

### 9.2 Data Flow

```
User Intent (YAML)
    ↓
Value Stream Controller (creates run)
    ↓
Agents (produce artifacts)
    ↓
Git (versions artifacts)
    ↓
SQLite (indexes metadata)
    ↓
Observability Collector (telemetry)
    ↓
Dashboards (outcomes, cost, quality)
```

## 10. Security Boundaries

### 10.1 Trust Boundaries

```
Trusted:
  - User-provided problem statements
  - Approved policies
  - Verified container images
  - Signed schemas

Untrusted:
  - LLM outputs
  - Agent-generated code
  - External APIs
  - User-uploaded files
```

### 10.2 Isolation Layers

1. **Network**: Containers have restricted egress
2. **Filesystem**: Read-only mounts except designated workspaces
3. **Process**: Non-root user, dropped capabilities
4. **Authorization**: Policy-gated tool access
5. **Input Validation**: Schema validation before processing
6. **Output Sanitization**: Filter dangerous constructs

## 11. Measurable Acceptance Criteria

### 11.1 Functional Acceptance

- [ ] **AC-F01**: Execute greenfield value stream run end-to-end
- [ ] **AC-F02**: Execute brownfield documentation bootstrap
- [ ] **AC-F03**: Policy denies unauthorized tool call with audit log
- [ ] **AC-F04**: Specification passes independent review agent
- [ ] **AC-F05**: Build agent produces passing tests
- [ ] **AC-F06**: Evaluation gate blocks failing artifact
- [ ] **AC-F07**: Cost tracking reports accurate token usage
- [ ] **AC-F08**: Traces visible in observability dashboard

### 11.2 Security Acceptance

- [ ] **AC-S01**: Container runs as non-root user (UID 1000)
- [ ] **AC-S02**: Prompt injection attempt is detected and blocked
- [ ] **AC-S03**: Agent cannot access filesystem outside workspace
- [ ] **AC-S04**: Secrets are never logged or sent to LLM
- [ ] **AC-S05**: Policy violation generates security event

### 11.3 Quality Acceptance

- [ ] **AC-Q01**: All unit tests pass in < 2 minutes
- [ ] **AC-Q02**: Code coverage > 80% for core modules
- [ ] **AC-Q03**: No critical security findings in SAST scan
- [ ] **AC-Q04**: JSON schemas validate successfully
- [ ] **AC-Q05**: Documentation includes: README, architecture, API reference, threat model

### 11.4 Usability Acceptance

- [ ] **AC-U01**: Fresh checkout can run `make demo` successfully
- [ ] **AC-U02**: Error messages include recovery suggestions
- [ ] **AC-U03**: All public APIs have JSON Schema documentation
- [ ] **AC-U04**: Architecture diagrams exist and are current

## 12. Traceability Map (Summary)

(See TRACEABILITY.md for complete mapping.)

| Requirement Category | Implementation Module | Test Suite | Documentation |
|---------------------|----------------------|------------|---------------|
| Value Stream (REQ-001-005) | orchestrator/ | tests/integration/test_value_stream.py | docs/architecture.md |
| Agent System (REQ-010-015) | agents/, harness/ | tests/unit/test_agents.py | docs/agent-guide.md |
| Discovery (REQ-020-024) | agents/discovery/ | tests/eval/discovery_eval.py | docs/discovery.md |
| Specification (REQ-030-034) | agents/specification/ | tests/eval/spec_eval.py | docs/specification.md |
| Build (REQ-040-044) | agents/builder/ | tests/integration/test_build.py | docs/build.md |
| Quality (REQ-050-054) | evaluation/ | tests/unit/test_evaluation.py | docs/evaluation.md |
| Policy (REQ-060-065) | policy/ | tests/unit/test_policy.py | docs/policy.md |
| Observability (REQ-070-075) | observability/ | tests/integration/test_telemetry.py | docs/observability.md |
| Security (REQ-100-106) | harness/sandbox/ | tests/security/ | SECURITY.md, docs/threat-model.md |

## 13. Success Metrics

The reference implementation is successful if it demonstrates:

1. **Traceability**: Every requirement has implementation + test + documentation
2. **Executability**: Fresh clone can run demo without manual configuration
3. **Observability**: All agent actions are traced and cost-tracked
4. **Security**: OWASP LLM Top 10 controls are implemented
5. **Quality**: Tests pass, evaluations validate behavior
6. **Understandability**: Engineers can understand architecture in < 1 hour
7. **Extensibility**: New agents can be added without modifying core
8. **Governance**: Policies are declarative, auditable, enforceable

## 14. References

- **SOURCE-01**: AI-native PDLC/SDLC strategic playbook (50KB)
- **SOURCE-02**: Implementation blueprint (37KB)
- **SOURCE-03**: Harness² clarification (13KB)
- **DORA**: DevOps Research and Assessment
- **OWASP LLM Top 10**: https://genai.owasp.org/llm-top-10/
- **OpenTelemetry GenAI**: https://github.com/open-telemetry/semantic-conventions-genai
- **Cedar Policy Language**: https://www.cedarpolicy.com/
- **JSON Schema 2020-12**: https://json-schema.org/specification

---

**Document Version**: 1.0.0  
**Status**: Canonical Specification  
**Last Updated**: 2026-11-18  
**Next Review**: After v0.1 MVP completion
