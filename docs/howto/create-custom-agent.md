# How-To: Create a Custom Agent

**Goal**: Add a new agent to the system that performs a custom task.

**Time**: 30-60 minutes

**Prerequisites**:
- Familiarity with Python async/await
- Understanding of [Agent Protocol](../reference/api/agents.md)

---

## Step 1: Define Agent Objective

Clearly define what your agent does. Example:

> **Objective**: Generate API documentation from code and specifications.

---

## Step 2: Create Agent Class

Create a new file: `src/product_value_system/agents/api_doc_agent.py`

```python
"""
API Documentation Generator Agent.
"""

from typing import Any

from product_value_system.adapters.model_adapter import Message, Role
from product_value_system.agents.agent import AgentResult
from product_value_system.agents.base_agent import BaseAgent
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness


class APIDocAgent(BaseAgent):
    """
    Agent that generates API documentation from specifications.
    """

    def __init__(self) -> None:
        super().__init__(
            name="api-doc-agent",
            version="0.1.0",
            capabilities=["analyze", "document", "generate"],
            required_tools=["spec.read", "template.load"],
            input_schema="https://pvs.ai/schemas/v0.1.0/specification.schema.json",
            output_schema="https://pvs.ai/schemas/v0.1.0/api_documentation.schema.json",
        )

    async def _execute_impl(
        self,
        input_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> AgentResult:
        """
        Generate API documentation from specification.

        Args:
            input_artifact: Specification artifact
            harness: Harness with tools and context
            context: Execution context

        Returns:
            AgentResult with API documentation
        """
        spec = input_artifact.get("specification", "")
        prompt = self._build_prompt(spec)

        response = await harness.invoke_model(
            messages=[
                Message(role=Role.SYSTEM, content=context.system_prompt),
                Message(role=Role.USER, content=prompt),
            ],
            temperature=0.2,
        )

        api_docs = self._parse_response(response.content)

        return AgentResult(
            success=True,
            output_artifact=api_docs,
            iterations=harness.budget.current_iterations,
            cost_usd=float(harness.budget.current_cost_usd),
            duration_seconds=0.0,
            reasoning_trace=["Generated API documentation from specification"],
            tool_calls=[],
            checkpoints=[],
        )

    def _build_prompt(self, spec: str) -> str:
        """Build prompt for API documentation generation."""
        return f"""Generate comprehensive API documentation from this specification:

{spec}

Include:
- Endpoint descriptions
- Request/response schemas
- Authentication requirements
- Examples

Format: Markdown"""

    def _parse_response(self, content: str) -> dict[str, Any]:
        """Parse LLM response into structured artifact."""
        return {
            "type": "api_documentation",
            "content": content,
            "format": "markdown",
        }
```

---

## Step 3: Create Agent Configuration

Create: `config/agents/api_doc_agent.yaml`

```yaml
apiVersion: pvs.ai/v1alpha1
kind: AgentDefinition

metadata:
  name: api-doc-agent
  version: 0.1.0
  description: Generate comprehensive API documentation from specifications.

spec:
  objective: >
    Generate comprehensive API documentation from specifications.

  capabilities:
    - analyze
    - document
    - generate

  model:
    provider: openai
    model: gpt-4
    temperature: 0.2

  allowedTools:
    - spec.read
    - template.load

  deniedTools:
    - repo.commit

  autonomy:
    maxIterations: 3
    maxCostUsd: 1.0
    maxDurationSeconds: 180
    humanApproval:
      requiredFor: []

  inputs:
    - type: specification
      schema: https://pvs.ai/schemas/v0.1.0/specification.schema.json
      required: true

  outputs:
    - type: api_documentation
      schema: https://pvs.ai/schemas/v0.1.0/api_documentation.schema.json
```

---

## Step 4: Define Output Schema

Create: `schemas/api_documentation.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://pvs.ai/schemas/v0.1.0/api_documentation.schema.json",
  "type": "object",
  "required": ["type", "content", "format"],
  "properties": {
    "type": {
      "type": "string",
      "const": "api_documentation"
    },
    "content": {
      "type": "string",
      "minLength": 100
    },
    "format": {
      "type": "string",
      "enum": ["markdown", "html", "openapi"]
    }
  }
}
```

---

## Step 5: Write Unit Tests

Create: `tests/unit/test_api_doc_agent.py`

```python
"""
Tests for API Documentation Agent.
"""

from decimal import Decimal
import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.agents.api_doc_agent import APIDocAgent
from product_value_system.harness.budget import BudgetConstraints
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.cost_tracker import CostTracker
from product_value_system.policy.engine import PolicyEngine


@pytest.mark.asyncio
async def test_api_doc_agent_generates_documentation() -> None:
    """Test API doc agent generates documentation."""
    mock_adapter = MockModelAdapter(
        responses=["# API Documentation\n\n## Endpoints\n\nGET /v1/payments..."]
    )
    agent = APIDocAgent()

    harness = Harness(
        agent_id="api-doc-agent",
        model_adapter=mock_adapter,
        policy_engine=PolicyEngine(),
        cost_tracker=CostTracker(),
        budget_constraints=BudgetConstraints(
            max_iterations=3,
            max_cost_usd=Decimal("1.0"),
            max_duration_seconds=60,
        ),
        allowed_tools=["spec.read", "template.load"],
        denied_tools=["repo.commit"],
    )
    context = ExecutionContext(system_prompt="You are a technical writer.")

    input_artifact = {"specification": "API for payment processing..."}
    result = await agent.execute(input_artifact, harness, context)

    assert result.success is True
    assert result.output_artifact["type"] == "api_documentation"
    assert "API Documentation" in result.output_artifact["content"]
```

Run tests:

```bash
pytest tests/unit/test_api_doc_agent.py -v
```

---

## Step 6: Register Agent

Register the agent with `AgentRegistry` in `src/product_value_system/agents/__init__.py` or during orchestrator startup:

```python
from product_value_system.agents.api_doc_agent import APIDocAgent
from product_value_system.agents.registry import AgentRegistry

registry = AgentRegistry()
registry.register(APIDocAgent())
```

---

## Step 7: Test Integration

Create integration test: `tests/integration/test_api_doc_agent.py`

```python
"""
Integration test for API Doc Agent.
"""

from decimal import Decimal
import os
import pytest

from product_value_system.adapters.openai_adapter import OpenAIModelAdapter
from product_value_system.agents.api_doc_agent import APIDocAgent
from product_value_system.harness.budget import BudgetConstraints
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.cost_tracker import CostTracker
from product_value_system.policy.engine import PolicyEngine


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif("OPENAI_API_KEY" not in os.environ, reason="Requires OPENAI_API_KEY")
async def test_api_doc_agent_with_real_model() -> None:
    """Test API doc agent with real OpenAI model."""
    model_adapter = OpenAIModelAdapter(api_key=os.environ["OPENAI_API_KEY"])
    agent = APIDocAgent()

    harness = Harness(
        agent_id="api-doc-agent",
        model_adapter=model_adapter,
        policy_engine=PolicyEngine(),
        cost_tracker=CostTracker(),
        budget_constraints=BudgetConstraints(
            max_iterations=3,
            max_cost_usd=Decimal("1.0"),
            max_duration_seconds=180,
        ),
        allowed_tools=["spec.read", "template.load"],
        denied_tools=["repo.commit"],
    )
    context = ExecutionContext(system_prompt="You are a technical writer.")

    result = await agent.execute(
        {"specification": "API for payment processing with credit cards..."},
        harness,
        context,
    )

    assert result.success is True
    assert result.output_artifact["type"] == "api_documentation"
    assert len(result.output_artifact["content"]) > 100
```

Run:

```bash
pytest tests/integration/test_api_doc_agent.py -v
```

---

## Step 8: Add to Orchestrator (Optional)

If the agent is part of the value stream, register it in the orchestrator's `AgentRegistry` (`src/product_value_system/orchestrator/controller.py`):

```python
self.agent_registry.register(APIDocAgent())
```

---

## Troubleshooting

### Agent not found in registry

**Error**: `KeyError: "Agent not registered: api-doc-agent"`

**Solution**: Ensure `registry.register(APIDocAgent())` is called before orchestrator execution (Step 6).

---

### Output schema validation fails

**Error**: `ValidationError: 'content' is a required property`

**Solution**: Check that `_parse_response()` returns all required fields from `schemas/api_documentation.schema.json`.

---

### Model call fails

**Error**: `ModelError: Invalid API key`

**Solution**: Ensure the `OPENAI_API_KEY` environment variable is set.

---

## Next Steps

- [Define policies](define-policies.md) for your agent
- [Write evaluations](write-evaluations.md) to test quality
- See [API Reference: Agents](../reference/api/agents.md) for details

---

**Last Updated**: 2026-11-18
