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

Conducts independent technical reviews of specifications with fresh context isolation (`harness.memory.clear_messages()`), producing `evidence.schema.json`-compliant review artifacts.

---

### Class: `SecurityReviewAgent`

**Source**: [`src/product_value_system/agents/security_review_agent.py`](../../../src/product_value_system/agents/security_review_agent.py)

Conducts independent security reviews of specifications with fresh context isolation, identifying authentication, authorization, data protection, and injection risks.

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
