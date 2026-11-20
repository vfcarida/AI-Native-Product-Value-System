# API Reference: Agents

## Module: `product_value_system.agents`

**Source**: [`src/product_value_system/agents/`](../../../src/product_value_system/agents/)

---

### Protocol: `Agent`

**Source**: [`src/product_value_system/agents/agent.py`](../../../src/product_value_system/agents/agent.py)

`@runtime_checkable` Protocol defining the interface for all agents in the system.

#### Properties
- `name: str` — Unique agent identifier (e.g., `"discovery-agent"`).
- `version: str` — Semantic version string (e.g., `"0.1.0"`).
- `capabilities: list[str]` — Capabilities provided by this agent.
- `required_tools: list[str]` — Tools required by this agent.
- `input_schema: str` — JSON Schema URI for expected input artifact.
- `output_schema: str` — JSON Schema URI for produced output artifact.

#### Method: `execute`

```python
async def execute(
    self,
    input_artifact: dict[str, Any],
    harness: Harness,
    context: ExecutionContext,
) -> AgentResult | dict[str, Any]:
```

---

### Class: `BaseAgent`

**Source**: [`src/product_value_system/agents/base_agent.py`](../../../src/product_value_system/agents/base_agent.py)

Base class providing input/output validation, harness budget tracking, and OpenTelemetry `@traced("agent.execute")` instrumentation.

---

### Class: `DiscoveryAgent`

**Source**: [`src/product_value_system/agents/discovery_agent.py`](../../../src/product_value_system/agents/discovery_agent.py)

Transforms validated `problem.yaml` artifacts into testable `outcome.yaml` hypotheses with primary success metrics, guardrails, and evidence requirements.

```python
agent = DiscoveryAgent(schema_validator=SchemaValidator("schemas"))
outcome = await agent.execute(problem_artifact, harness, context)
```

---

### Class: `SpecificationAgent`

**Source**: [`src/product_value_system/agents/specification_agent.py`](../../../src/product_value_system/agents/specification_agent.py)

Transforms validated `outcome.yaml` hypotheses into detailed `specification.yaml` contracts containing functional requirements, acceptance criteria, architecture decisions, quality attributes, testability criteria, and constraints.

---

### Class: `ArchitectureReviewAgent`

**Source**: [`src/product_value_system/agents/architecture_review_agent.py`](../../../src/product_value_system/agents/architecture_review_agent.py)

Subclasses `BaseReviewAgent`. Conducts independent technical reviews of specifications with fresh Goldfish context isolation (`harness.memory.clear()`), producing `evidence.schema.json`-compliant review artifacts.

---

### Class: `SecurityReviewAgent`

**Source**: [`src/product_value_system/agents/security_review_agent.py`](../../../src/product_value_system/agents/security_review_agent.py)

Subclasses `BaseReviewAgent`. Conducts independent OWASP LLM Top 10 & AppSec reviews (LLM01, LLM02, LLM04, LLM06, LLM07, LLM08) with fresh context isolation.

---

### Class: `PerformanceReviewAgent`

**Source**: [`src/product_value_system/agents/performance_review_agent.py`](../../../src/product_value_system/agents/performance_review_agent.py)

Subclasses `BaseReviewAgent`. Conducts independent latency, algorithmic complexity, memory, N+1 I/O, caching, and scalability reviews in Goldfish mode.

---

### Class: `MaintainabilityReviewAgent`

**Source**: [`src/product_value_system/agents/maintainability_review_agent.py`](../../../src/product_value_system/agents/maintainability_review_agent.py)

Subclasses `BaseReviewAgent`. Conducts independent requirement clarity, acceptance-criteria completeness, testability, documentation, and technical-debt reviews in Goldfish mode.

---

### Multi-Agent Review Orchestration (`product_value_system.review`)

**Sources**:
- [`src/product_value_system/review/models.py`](../../../src/product_value_system/review/models.py) — `FindingSeverity`, `FindingCategory`, `ReviewFinding`, `ReviewConflict`, `ReviewResult`
- [`src/product_value_system/review/synthesizer.py`](../../../src/product_value_system/review/synthesizer.py) — `ReviewSynthesizer` (aggregation, conflict detection & resolution, release gate decision)
- [`src/product_value_system/review/orchestrator.py`](../../../src/product_value_system/review/orchestrator.py) — `MultiAgentReviewOrchestrator` & `execute_multi_agent_review()` (concurrent `asyncio.gather()` execution across all 4 independent perspectives)

---

### Class: `BuildAgent`

**Source**: [`src/product_value_system/agents/build_agent.py`](../../../src/product_value_system/agents/build_agent.py)

Generates implementation code and pytest suites per functional requirement, executes tests inside an isolated Docker sandbox (`SandboxProvider`), and creates incremental Git commits.

---

### Class: `AgentRegistry` & `AgentConfig`

**Sources**:
- [`src/product_value_system/agents/registry.py`](../../../src/product_value_system/agents/registry.py)
- [`src/product_value_system/agents/config_loader.py`](../../../src/product_value_system/agents/config_loader.py)

Manages agent registration, capability lookup (`list_by_capability`), and YAML configuration loading (`AgentConfig.from_yaml`).
