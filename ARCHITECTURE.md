# AI-Native Product Value System: Architecture

## 1. Executive Summary

This document defines the technical architecture for the AI-native Product Value System reference implementation. The architecture demonstrates how to build quality, security, and governance into AI-accelerated product development.

**Core Architectural Principles**:
1. **Contracts Over Implementations** — JSON Schema contracts enable adapter pattern
2. **Evidence-Based Progression** — No stage advances without validated evidence
3. **Isolation by Default** — Agents execute in sandboxes with policy-gated tool access
4. **Observable by Design** — Every interaction traced with OpenTelemetry
5. **Modular and Extensible** — Swap models, agents, tools without changing core

## 2. System Context

### 2.1 System Boundary

```
┌─────────────────────────────────────────────────────────────┐
│                  AI-Native Product Value System              │
│                                                              │
│  Problem → Outcome → Discovery → Spec → Build → Review      │
│           → Release → Run → Learn → [feedback]              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
        ↕                    ↕                    ↕
   [User Input]      [Model APIs]         [Git Repository]
   [Problem YAML]    [OpenAI/Anthropic]   [Artifacts]
   [Approvals]       [Google/Azure]       [Code/Docs/Tests]
```

**Inside Boundary**:
- Value Stream Orchestrator
- Agents (Discovery, Spec, Build, Review, Security, Release, Learn)
- Harness components (Context, Tools, Memory, Guardrails)
- Policy Engine (Cedar authorization)
- Evaluation Framework
- Sandbox Runtime (Docker containers)
- Observability (OpenTelemetry instrumentation)

**Outside Boundary**:
- Foundation model APIs (OpenAI, Anthropic, Google, Azure, local)
- User interfaces (CLI, potential future web UI)
- Git repository (artifact storage)
- Observability backends (Jaeger, Prometheus, etc.)
- External knowledge sources (documentation, APIs)

### 2.2 Deployment Model (MVP)

**Single-Machine Deployment**:
- Docker Desktop or Docker Engine
- Python 3.12+ runtime
- SQLite database (run metadata, policy decisions)
- Local filesystem (Git repository for artifacts)
- Local OpenTelemetry Collector (optional)

**Network**:
- Outbound HTTPS to model providers
- No inbound ports (local execution only)
- Container-to-container networking for orchestration

**Future**: Kubernetes, distributed orchestration, cloud deployment.

## 3. Architectural Layers

### 3.1 Layer Overview

```
┌─────────────────────────────────────────────────────────────┐
│ L7: Observability & Measurement                              │
│     OpenTelemetry, Cost Tracking, Metrics, Dashboards        │
├─────────────────────────────────────────────────────────────┤
│ L6: Orchestration & Workflow                                 │
│     Value Stream Controller, State Machine, Checkpoints      │
├─────────────────────────────────────────────────────────────┤
│ L5: Agent System                                             │
│     Discovery, Spec, Build, Review, Security, Release, Learn │
├─────────────────────────────────────────────────────────────┤
│ L4: Harness & Control                                        │
│     Context, Tools, Memory, Guardrails, Budgets              │
├─────────────────────────────────────────────────────────────┤
│ L3: Policy & Authorization                                   │
│     Cedar Policy Engine, Entity Schemas, Audit Log           │
├─────────────────────────────────────────────────────────────┤
│ L2: Adapters & Integration                                   │
│     Model Adapter, Knowledge Adapter, Sandbox Provider       │
├─────────────────────────────────────────────────────────────┤
│ L1: Runtime & Isolation                                      │
│     Docker Containers, Non-root User, Capability Restrictions│
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Layer Responsibilities

**L1: Runtime & Isolation**
- Provide isolated execution environments
- Enforce process/network/filesystem boundaries
- Run as non-root with minimal capabilities
- Implement resource limits (CPU, memory, disk)

**L2: Adapters & Integration**
- Abstract external dependencies (models, knowledge, sandboxes)
- Enable swapping providers without changing core
- Translate between external APIs and internal contracts

**L3: Policy & Authorization**
- Evaluate agent actions against declarative policies
- Enforce least-privilege access to tools and data
- Maintain audit log of authorization decisions
- Support schema validation of policies

**L4: Harness & Control**
- Manage agent context (system prompts, examples, memory)
- Provide tool access with safety wrappers
- Implement guardrails (iteration limits, cost budgets, timeouts)
- Support checkpoint/recovery for long-running work

**L5: Agent System**
- Implement specialized agents for different value stream stages
- Follow single-responsibility principle (one agent, one job)
- Operate within harness constraints
- Produce structured, validatable outputs

**L6: Orchestration & Workflow**
- Coordinate multi-agent workflows
- Manage state transitions through value stream
- Enforce evidence-based progression
- Support human-in-the-loop approvals
- Implement checkpointing and recovery

**L7: Observability & Measurement**
- Instrument all interactions with OpenTelemetry
- Track costs (tokens, compute, time)
- Measure quality (test pass rates, eval scores)
- Attribute outcomes to agents and models
- Support dashboards and analysis

## 4. Component Architecture

### 4.1 Value Stream Orchestrator

**Responsibility**: Coordinate complete problem → outcome flow.

```python
class ValueStreamOrchestrator:
    """
    Orchestrates a single value stream run from problem to outcome.
    """
    
    def __init__(
        self,
        agents: AgentRegistry,
        policy_engine: PolicyEngine,
        sandbox_provider: SandboxProvider,
        observability: ObservabilityProvider
    ):
        ...
    
    async def execute_run(
        self,
        problem: ProblemStatement,
        config: RunConfig
    ) -> ValueStreamResult:
        """
        Execute complete value stream run.
        
        Stages:
        1. Problem analysis
        2. Outcome hypothesis generation
        3. Discovery and evidence gathering
        4. Decision gate (human approval if needed)
        5. Specification generation
        6. Independent review
        7. Build (in sandbox)
        8. Quality gates (tests, evals, security)
        9. Release decision
        10. Outcome measurement
        """
        ...
```

**State Management**:
- Each run has unique ID (UUID)
- State persisted to SQLite after each stage
- Git commits for all artifacts
- Checkpoints enable recovery

**Concurrency**:
- Sequential stages (discovery → spec → build)
- Parallel reviews (architecture + security)
- Parallel evals (multiple judges)

### 4.2 Agent System

**Agent Abstraction**:

```python
class Agent(Protocol):
    """
    Protocol for all agents in the system.
    """
    
    @property
    def name(self) -> str:
        """Unique agent identifier."""
        ...
    
    @property
    def capabilities(self) -> List[str]:
        """What this agent can do."""
        ...
    
    async def execute(
        self,
        input_artifact: Artifact,
        harness: Harness,
        context: ExecutionContext
    ) -> AgentResult:
        """
        Execute agent task.
        
        Args:
            input_artifact: Structured input (e.g., problem.yaml, spec.md)
            harness: Harness providing context, tools, memory
            context: Run metadata, budgets, permissions
        
        Returns:
            AgentResult with output artifact and telemetry
        """
        ...
```

**Agent Types (MVP)**:

1. **DiscoveryAgent**: Analyzes problems, generates outcome hypotheses
2. **SpecificationAgent**: Produces specifications from outcomes
3. **ArchitectureReviewAgent**: Reviews specs for technical soundness
4. **SecurityReviewAgent**: Reviews specs for security risks
5. **BuildAgent**: Implements specifications in code
6. **TestGeneratorAgent**: Generates tests from specifications
7. **ReleaseReviewAgent**: Evaluates release readiness
8. **LearningAgent**: Analyzes outcomes and generates insights

**Agent Configuration (YAML)**:

```yaml
apiVersion: pvs.ai/v1alpha1
kind: AgentDefinition

metadata:
  name: specification-agent
  version: 0.1.0

spec:
  objective: >
    Generate detailed, testable specifications from outcome hypotheses.
  
  model:
    provider: openai
    model: gpt-4
    temperature: 0.2
  
  allowedTools:
    - knowledge.query
    - spec.template.load
    - spec.validate
  
  deniedTools:
    - repo.commit
    - prod.deploy
  
  autonomy:
    maxIterations: 5
    maxCostUsd: 1.50
    humanApproval:
      requiredFor:
        - new.external.dependency
  
  outputs:
    - type: specification
      schema: schemas/specification.schema.json
```

### 4.3 Harness System

**Harness Components** (inspired by Harness²):

```python
class Harness:
    """
    Harness wrapping an agent to control behavior.
    """
    
    def __init__(
        self,
        agent: Agent,
        system_prompt: SystemPrompt,
        guardrails: Guardrails,
        memory: Memory,
        tool_guidance: ToolGuidance,
        skills: List[Skill],
        budget: Budget
    ):
        ...
    
    async def execute(
        self,
        task: Task,
        context: ExecutionContext
    ) -> HarnessResult:
        """
        Execute task with agent, enforcing all harness constraints.
        """
        ...
```

**Components**:

1. **SystemPrompt**: Agent instructions, examples, constraints
2. **Guardrails**: Iteration limits, content filters, safety checks
3. **Memory**: Short-term (conversation) + long-term (artifacts)
4. **ToolGuidance**: When/how to use tools, examples, anti-patterns
5. **Skills**: Reusable capabilities (e.g., "analyze code", "write tests")
6. **Budget**: Cost (USD), time (seconds), iterations (count)

**Checkpoint Management**:

```python
class Checkpoint:
    """
    Persistent checkpoint for long-running work.
    """
    
    run_id: str
    stage: str
    timestamp: datetime
    state: Dict[str, Any]
    artifacts: List[str]  # Git refs
    next_action: str
```

### 4.4 Policy Engine

**Architecture**:

```
Agent wants to call tool
        ↓
Harness intercepts call
        ↓
Policy Engine evaluates
        ↓
Cedar policy decision
        ↓
[ALLOW] → Execute tool
[DENY]  → Log + raise exception
```

**Cedar Entity Schema**:

```cedar
entity Agent {
  role: AgentRole,
  trustLevel: String,
  costBudget: Decimal,
  allowedEnvironments: Set<String>
};

entity Tool {
  classification: String,  // "read", "write", "external"
  riskLevel: String,       // "low", "medium", "high"
  dataAccess: Set<String>
};

entity Action {
  type: String  // "read", "write", "execute", "deploy"
};
```

**Example Policy**:

```cedar
// Allow discovery agent to query knowledge base
permit (
  principal in AgentRole::"discovery",
  action == Action::"query",
  resource in Tool::"knowledge_base"
) when {
  context.environment == "sandbox"
};

// Deny any agent from deploying to production
forbid (
  principal,
  action == Action::"deploy",
  resource in Environment::"production"
) unless {
  context.has_human_approval == true
};

// Require human approval for external API calls
permit (
  principal,
  action == Action::"call",
  resource in Tool::"external_api"
) when {
  context.has_human_approval == true &&
  principal.trustLevel == "high"
};
```

**Policy Validation**: All policies validated against schema in CI.

### 4.5 Sandbox Runtime

**Docker Container Architecture**:

```
┌─────────────────────────────────────────────┐
│ Container: build-agent-<run-id>             │
│                                             │
│  User: pvs (UID 1000, non-root)            │
│  Capabilities: NONE (all dropped)           │
│  Network: restricted egress                 │
│  Filesystem:                                │
│    /workspace (rw, mounted volume)         │
│    /app (ro, application code)             │
│    /tmp (rw, tmpfs)                        │
│  Resources:                                 │
│    CPU: 2 cores                            │
│    Memory: 4GB                             │
│    Disk: 10GB                              │
│    Time: 15 minutes                        │
│                                             │
│  Tools: git, python3, make, pytest         │
└─────────────────────────────────────────────┘
```

**Sandbox Interface**:

```python
class SandboxProvider(Protocol):
    """
    Abstract interface for execution isolation.
    """
    
    async def create(
        self,
        config: SandboxConfig
    ) -> Sandbox:
        """Create isolated sandbox."""
        ...
    
    async def execute(
        self,
        sandbox: Sandbox,
        command: str,
        timeout_seconds: int
    ) -> ExecutionResult:
        """Execute command in sandbox."""
        ...
    
    async def copy_in(
        self,
        sandbox: Sandbox,
        local_path: str,
        sandbox_path: str
    ) -> None:
        """Copy file into sandbox."""
        ...
    
    async def copy_out(
        self,
        sandbox: Sandbox,
        sandbox_path: str,
        local_path: str
    ) -> None:
        """Copy file out of sandbox."""
        ...
    
    async def destroy(self, sandbox: Sandbox) -> None:
        """Destroy sandbox."""
        ...
```

**MVP Implementation**: DockerSandboxProvider

**Future**: E2B, Kubernetes Jobs, microVMs (Firecracker)

### 4.6 Model Adapter

**Abstraction**:

```python
class ModelAdapter(Protocol):
    """
    Abstract interface for LLM providers.
    """
    
    async def invoke(
        self,
        messages: List[Message],
        tools: Optional[List[Tool]] = None,
        **kwargs
    ) -> ModelResponse:
        """
        Invoke model with messages and tools.
        
        Returns:
            ModelResponse with content, tool_calls, usage, finish_reason
        """
        ...
    
    async def stream(
        self,
        messages: List[Message],
        tools: Optional[List[Tool]] = None,
        **kwargs
    ) -> AsyncIterator[ModelChunk]:
        """Stream model response."""
        ...
```

**Implementations**:

- `OpenAIModelAdapter`
- `AnthropicModelAdapter`
- `GoogleModelAdapter`
- `AzureModelAdapter`
- `LocalModelAdapter` (for self-hosted models)

**Model Gateway (Future)**:

```python
class ModelGateway:
    """
    Routes requests to optimal model based on:
    - Capability required (reasoning, code, vision)
    - Cost budget
    - Latency requirement
    - Data classification
    """
    
    async def invoke(
        self,
        capability: str,
        messages: List[Message],
        policy: InvocationPolicy
    ) -> ModelResponse:
        """Route to appropriate model."""
        ...
```

### 4.7 Evaluation Framework

**Architecture**:

```
Artifact (e.g., generated spec)
        ↓
Evaluation Pipeline
        ↓
Multiple Judges (parallel)
   ├─→ Completeness Judge
   ├─→ Clarity Judge
   ├─→ Security Judge
   ├─→ Testability Judge
        ↓
Aggregate Scores
        ↓
Pass/Fail Decision
        ↓
Evidence Artifact
```

**Judge Interface**:

```python
class Judge(Protocol):
    """
    Evaluates an artifact against criteria.
    """
    
    async def evaluate(
        self,
        artifact: Artifact,
        criteria: EvaluationCriteria,
        context: Optional[Dict] = None
    ) -> JudgeResult:
        """
        Evaluate artifact.
        
        Returns:
            JudgeResult with score (0.0-1.0), rationale, pass/fail
        """
        ...
```

**Judge Types**:

1. **Deterministic**: Schema validation, lint, tests
2. **LLM-based**: Quality, clarity, completeness, security
3. **Trajectory**: Tool selection, reasoning, iteration efficiency

**Golden Dataset**:

```
golden_dataset/
├── problem_statements/
│   ├── 001_payment_flow.yaml
│   ├── 002_auth_system.yaml
│   └── ...
├── expected_outcomes/
│   ├── 001_expected.yaml
│   └── ...
└── trajectories/
    ├── 001_acceptable_trajectory.json
    └── ...
```

### 4.8 Observability

**OpenTelemetry Instrumentation**:

```
value_stream.run (trace)
├── span: discovery.agent
│   ├── span: model.invoke (gen_ai.request.model: gpt-4)
│   │   ├── attribute: gen_ai.usage.input_tokens: 1024
│   │   ├── attribute: gen_ai.usage.output_tokens: 512
│   │   └── attribute: gen_ai.response.finish_reason: stop
│   ├── span: tool.call (tool.name: knowledge.query)
│   └── span: artifact.store
├── span: specification.agent
│   └── ...
├── span: review.architecture
│   └── ...
└── span: policy.evaluate
    ├── attribute: policy.decision: allow
    └── attribute: policy.rule: discovery_knowledge_access
```

**Metrics (Prometheus-compatible)**:

```
pvs_value_stream_duration_seconds{stage="discovery", outcome="success"}
pvs_agent_invocations_total{agent="specification", outcome="success"}
pvs_model_tokens_total{model="gpt-4", type="input"}
pvs_cost_usd_total{agent="discovery", model="gpt-4"}
pvs_policy_evaluations_total{decision="allow"}
pvs_policy_evaluations_total{decision="deny"}
pvs_sandbox_executions_total{outcome="success"}
pvs_eval_score{judge="completeness", artifact_type="specification"}
```

**Cost Attribution**:

```python
@dataclass
class CostBreakdown:
    """Cost breakdown for a value stream run."""
    
    run_id: str
    total_usd: Decimal
    
    by_agent: Dict[str, Decimal]
    by_model: Dict[str, Decimal]
    by_stage: Dict[str, Decimal]
    
    token_count: int
    compute_seconds: int
```

## 5. Data Architecture

### 5.1 Artifact Schemas

All artifacts use JSON Schema 2020-12.

**Problem Statement** (`schemas/problem.schema.json`):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://pvs.ai/schemas/problem.schema.json",
  "type": "object",
  "required": ["id", "version", "statement", "context", "constraints"],
  "properties": {
    "id": {"type": "string", "pattern": "^problem-[a-z0-9-]+$"},
    "version": {"type": "integer", "minimum": 1},
    "statement": {"type": "string", "minLength": 10},
    "context": {
      "type": "object",
      "properties": {
        "user_segment": {"type": "string"},
        "business_context": {"type": "string"}
      }
    },
    "constraints": {
      "type": "object",
      "properties": {
        "no_pii": {"type": "boolean"},
        "synthetic_only": {"type": "boolean"}
      }
    }
  }
}
```

**Outcome Hypothesis** (`schemas/outcome.schema.json`):
- Links to problem (via ID)
- Hypothesis statement
- Success metrics (baseline, target, guardrails)
- Evidence requirements

**Specification** (`schemas/specification.schema.json`):
- Links to outcome
- Functional requirements
- Architecture decisions
- Quality attributes
- Testability criteria

**Evidence** (`schemas/evidence.schema.json`):
- Type (test_result, review, measurement)
- Verdict (pass/fail/warn)
- Rationale
- Artifacts (references)

### 5.2 Database Schema (SQLite)

```sql
CREATE TABLE value_stream_runs (
    id TEXT PRIMARY KEY,
    problem_id TEXT NOT NULL,
    status TEXT NOT NULL,  -- pending, running, completed, failed
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    total_cost_usd REAL,
    outcome_achieved BOOLEAN
);

CREATE TABLE stages (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    agent_name TEXT,
    status TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    artifact_path TEXT,
    FOREIGN KEY (run_id) REFERENCES value_stream_runs(id)
);

CREATE TABLE policy_decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    agent_name TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    decision TEXT NOT NULL,  -- allow, deny
    policy_matched TEXT,
    FOREIGN KEY (run_id) REFERENCES value_stream_runs(id)
);

CREATE TABLE evaluations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    judge_name TEXT NOT NULL,
    score REAL NOT NULL,
    pass BOOLEAN NOT NULL,
    rationale TEXT,
    FOREIGN KEY (run_id) REFERENCES value_stream_runs(id)
);

CREATE TABLE costs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    agent_name TEXT,
    model_name TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES value_stream_runs(id)
);
```

### 5.3 Git Repository Structure

```
artifacts/
├── runs/
│   └── <run-id>/
│       ├── problem.yaml
│       ├── outcome.yaml
│       ├── discovery/
│       │   ├── evidence.json
│       │   └── synthesis.md
│       ├── specification/
│       │   ├── spec.md
│       │   └── architecture.md
│       ├── review/
│       │   ├── architecture_review.json
│       │   └── security_review.json
│       ├── implementation/
│       │   ├── src/
│       │   └── tests/
│       └── release/
│           ├── release_decision.json
│           └── evidence/
└── templates/
    ├── problem_template.yaml
    ├── outcome_template.yaml
    └── spec_template.md
```

Every stage commits artifacts with meaningful messages:

```
[run-abc123] Stage: discovery - Evidence synthesis completed

- Analyzed 15 synthetic customer interviews
- Identified 3 key pain points
- Generated 5 hypotheses
- Agent: discovery-agent v0.1.0
- Cost: $0.42
```

## 6. Security Architecture

### 6.1 Threat Model

(See SECURITY_AND_OPERATIONS.md for full threat model)

**Key Threats**:
1. **Prompt Injection**: Malicious input alters agent behavior
2. **Tool Abuse**: Agent misuses authorized tools
3. **Secret Exfiltration**: Agent leaks credentials or sensitive data
4. **Sandbox Escape**: Agent breaks isolation
5. **Policy Bypass**: Agent circumvents authorization

**Mitigations**:
1. Input validation (schema enforcement)
2. Policy-gated tool access (Cedar)
3. Secret management (never pass to LLM)
4. Container isolation (non-root, capabilities dropped)
5. Policy validation (schema-checked in CI)

### 6.2 Trust Boundaries

```
┌─────────────────────────────────────────┐
│ Trusted Zone                            │
│  - User input (validated)               │
│  - Approved policies                    │
│  - Signed container images              │
│  - Validated schemas                    │
└─────────────────────────────────────────┘
           ↕ (validation)
┌─────────────────────────────────────────┐
│ Untrusted Zone                          │
│  - LLM outputs                          │
│  - Agent-generated code                 │
│  - External API responses               │
│  - User-uploaded files (future)         │
└─────────────────────────────────────────┘
```

**Never Trust**:
- LLM output is treated as untrusted input
- Validate before execution
- Sanitize before storage
- Audit before authorization

## 7. Extension Points

### 7.1 Adapter Pattern

All external dependencies accessed via adapters:

```python
# Easy to swap providers
model_adapter = OpenAIModelAdapter(api_key=...)
# or
model_adapter = AnthropicModelAdapter(api_key=...)
# or
model_adapter = LocalModelAdapter(endpoint=...)

# Core logic unchanged
agent = SpecificationAgent(model=model_adapter)
```

### 7.2 Plugin System (Future)

```python
class AgentPlugin(Protocol):
    """
    Plugin extending agent capabilities.
    """
    
    def name(self) -> str:
        ...
    
    def before_execution(
        self,
        agent: Agent,
        task: Task
    ) -> Task:
        """Modify task before execution."""
        ...
    
    def after_execution(
        self,
        agent: Agent,
        result: AgentResult
    ) -> AgentResult:
        """Post-process result."""
        ...
```

### 7.3 Custom Agents

Users can add custom agents by:

1. Implementing `Agent` protocol
2. Defining agent config (YAML)
3. Adding policies for tool access
4. Registering with orchestrator

## 8. Deployment Architecture (MVP)

```
┌────────────────────────────────────────────────────────┐
│ Developer Machine                                       │
│                                                         │
│  ┌──────────────────────────────────────────────────┐ │
│  │ Python Process (Orchestrator)                    │ │
│  │  - ValueStreamOrchestrator                       │ │
│  │  - AgentRegistry                                 │ │
│  │  - PolicyEngine (Cedar)                          │ │
│  │  - ObservabilityProvider (OTel)                  │ │
│  └──────────────────────────────────────────────────┘ │
│           ↕                    ↕                       │
│  ┌────────────────┐   ┌────────────────────────────┐ │
│  │ SQLite DB      │   │ Docker Containers          │ │
│  │ - Runs         │   │  ┌──────────────────────┐  │ │
│  │ - Stages       │   │  │ build-agent-sandbox  │  │ │
│  │ - Policies     │   │  │  - Non-root user     │  │ │
│  │ - Costs        │   │  │  - Isolated FS       │  │ │
│  └────────────────┘   │  │  - Network restricted│  │ │
│                       │  └──────────────────────┘  │ │
│                       └────────────────────────────┘ │
│           ↕                                           │
│  ┌──────────────────────────────────────────────────┐ │
│  │ Git Repository (artifacts/)                      │ │
│  │  - Problem statements                            │ │
│  │  - Specifications                                │ │
│  │  - Code                                          │ │
│  │  - Evidence                                      │ │
│  └──────────────────────────────────────────────────┘ │
│                                                         │
│  Outbound:                                             │
│   → OpenAI API / Anthropic API / Google API            │
│   → PyPI (for dependencies)                            │
└────────────────────────────────────────────────────────┘
```

## 9. Scalability Considerations (Future)

MVP is single-machine. Future scaling path:

**Horizontal Scaling**:
- Orchestrator becomes stateless, backed by PostgreSQL
- Agents deployed as Kubernetes Jobs
- Sandboxes as Kubernetes Pods with resource limits
- Queue-based work distribution (RabbitMQ, Redis)

**Distributed Orchestration**:
- Temporal for durable workflows (survive orchestrator crashes)
- State persisted externally
- Checkpointing enables work distribution

**Model Gateway**:
- Centralized gateway with routing logic
- Caching layer (Redis) for repeated prompts
- Rate limiting, quota management
- Cost optimization (cheap models for simple tasks)

## 10. Technology Decisions Summary

| Decision | Choice | Rationale | Alternatives Considered |
|----------|--------|-----------|------------------------|
| Language | Python 3.12+ | Ecosystem, agent tooling, readability | TypeScript, Go |
| Packaging | pyproject.toml + src | PyPA best practice | setup.py (deprecated) |
| Contracts | JSON Schema 2020-12 | Vendor-neutral, tooling support | Pydantic, Protocol Buffers |
| Policy | Cedar | Readable, schema validation, ABAC | OPA (more complex), custom |
| Sandbox | Docker | Ubiquitous, good isolation | E2B (future), Kubernetes, VMs |
| Orchestration | State machine | Simple, local, no dependencies | Temporal (future for HA) |
| Observability | OpenTelemetry | Standard, GenAI conventions | Custom logging |
| Database | SQLite | Local, zero-config, sufficient for MVP | PostgreSQL (future) |
| Testing | pytest | Industry standard | unittest, nose |
| Model Adapter | Custom protocol | Flexibility, vendor neutrality | LiteLLM (future gateway) |

## 11. Visual Architecture Diagrams

Complete C4 (Level 1 System Context, Level 2 Container, Level 3 Harness Component), Sequence (Value Stream Run, Policy Evaluation), Trust Boundary, Data Flow, and Deployment diagrams are maintained in [docs/architecture/DIAGRAMS.md](docs/architecture/DIAGRAMS.md) and `docs/architecture/diagrams/*.mmd`:

1. [01-system-context.mmd](docs/architecture/diagrams/01-system-context.mmd) — C4 Level 1 System Context
2. [02-container-diagram.mmd](docs/architecture/diagrams/02-container-diagram.mmd) — C4 Level 2 Container Diagram
3. [03-component-diagram.mmd](docs/architecture/diagrams/03-component-diagram.mmd) — C4 Level 3 Harness Component Diagram
4. [04-value-stream-sequence.mmd](docs/architecture/diagrams/04-value-stream-sequence.mmd) — Value Stream Run Sequence
5. [05-policy-evaluation-sequence.mmd](docs/architecture/diagrams/05-policy-evaluation-sequence.mmd) — Policy Evaluation Sequence
6. [06-trust-boundaries.mmd](docs/architecture/diagrams/06-trust-boundaries.mmd) — Security Trust Boundaries
7. [07-data-flow.mmd](docs/architecture/diagrams/07-data-flow.mmd) — Artifact Data Flow Lifecycle
8. [08-deployment.mmd](docs/architecture/diagrams/08-deployment.mmd) — MVP Single-Machine Deployment

---

**Document Version**: 1.0.0  
**Status**: Canonical Architecture  
**Last Updated**: 2026-11-18  
**Next Review**: After ADRs from implementation
