"""
Unit tests for agent protocol and base implementation (P008).
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from product_value_system.agents.agent import Agent, AgentResult
from product_value_system.agents.base_agent import BaseAgent
from product_value_system.agents.config_loader import AgentConfig
from product_value_system.agents.registry import AgentRegistry
from product_value_system.harness import (
    Budget,
    ExecutionContext,
    Guardrails,
    Harness,
)
from product_value_system.policy.engine import PolicyEngine


class MockAgent(BaseAgent):
    """Mock agent for testing."""

    async def _execute_impl(
        self,
        input_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> AgentResult:
        """Simple mock implementation."""
        return AgentResult(
            success=True,
            output_artifact={"result": "mock output"},
            iterations=1,
            cost_usd=0.1,
            duration_seconds=0.5,
            reasoning_trace=["step 1", "step 2"],
            tool_calls=[],
            checkpoints=[],
        )


@pytest.mark.unit
def test_agent_protocol() -> None:
    """Test agent protocol properties and runtime_checkable conformance."""
    agent = MockAgent(
        name="mock-agent",
        version="0.1.0",
        capabilities=["mock"],
        required_tools=["mock.tool"],
        input_schema="mock.schema.json",
        output_schema="mock.output.json",
    )

    assert isinstance(agent, Agent)
    assert agent.name == "mock-agent"
    assert agent.version == "0.1.0"
    assert "mock" in agent.capabilities
    assert "mock.tool" in agent.required_tools


@pytest.mark.unit
@pytest.mark.asyncio
async def test_base_agent_execution() -> None:
    """Test base agent execution."""
    agent = MockAgent(
        name="test-agent",
        version="0.1.0",
        capabilities=["test"],
        required_tools=[],
        input_schema="",
        output_schema="",
    )

    context = ExecutionContext(
        run_id="run-123",
        stage_name="test",
        agent_name="test-agent",
        system_prompt="Test prompt",
    )

    policy_engine = PolicyEngine()
    budget = Budget(max_cost_usd=Decimal("1.0"))
    guardrails = Guardrails(max_iterations=5)

    harness = Harness(
        context=context,
        policy_engine=policy_engine,
        budget=budget,
        guardrails=guardrails,
    )

    result = await agent.execute(
        input_artifact={"test": "input"},
        harness=harness,
        context=context,
    )

    assert result.success
    assert result.output_artifact["result"] == "mock output"
    assert result.iterations == 1
    assert result.cost_usd == 0.1


@pytest.mark.unit
def test_agent_config_from_yaml(tmp_path: Path) -> None:
    """Test loading agent config from YAML."""
    config_yaml = """
apiVersion: pvs.ai/v1alpha1
kind: AgentDefinition

metadata:
  name: test-agent
  version: 0.2.0

spec:
  objective: Test agent
  capabilities:
    - test.capability
  requiredTools:
    - test.tool
  model:
    provider: openai
    model: gpt-4
    temperature: 0.3
  allowedTools:
    - tool.allowed
  autonomy:
    maxIterations: 7
    maxCostUsd: 2.5
"""

    config_file = tmp_path / "test-agent.yaml"
    config_file.write_text(config_yaml, encoding="utf-8")

    config = AgentConfig.from_yaml(config_file)

    assert config.name == "test-agent"
    assert config.version == "0.2.0"
    assert config.model_name == "gpt-4"
    assert config.temperature == 0.3
    assert config.max_iterations == 7
    assert config.max_cost_usd == 2.5


@pytest.mark.unit
def test_agent_registry() -> None:
    """Test agent registry."""
    registry = AgentRegistry()

    agent = MockAgent(
        name="agent-1",
        version="0.1.0",
        capabilities=["cap1"],
        required_tools=[],
        input_schema="",
        output_schema="",
    )

    registry.register(agent)

    retrieved = registry.get("agent-1")
    assert retrieved is not None
    assert retrieved.name == "agent-1"

    agents = registry.list_agents()
    assert "agent-1" in agents

    registry.unregister("agent-1")
    assert registry.get("agent-1") is None


@pytest.mark.unit
def test_agent_registry_by_capability() -> None:
    """Test finding agents by capability."""
    registry = AgentRegistry()

    agent1 = MockAgent(
        name="agent-1",
        version="0.1.0",
        capabilities=["discovery"],
        required_tools=[],
        input_schema="",
        output_schema="",
    )

    agent2 = MockAgent(
        name="agent-2",
        version="0.1.0",
        capabilities=["specification"],
        required_tools=[],
        input_schema="",
        output_schema="",
    )

    registry.register(agent1)
    registry.register(agent2)

    discovery_agents = registry.list_by_capability("discovery")
    assert len(discovery_agents) == 1
    assert discovery_agents[0].name == "agent-1"


@pytest.mark.unit
def test_agent_result_serialization() -> None:
    """Test AgentResult serialization."""
    result = AgentResult(
        success=True,
        output_artifact={"key": "value"},
        iterations=3,
        cost_usd=0.5,
        duration_seconds=2.5,
        reasoning_trace=["step1", "step2"],
        tool_calls=[{"tool": "test", "result": "ok"}],
        checkpoints=["checkpoint-1"],
    )

    result_dict = result.to_dict()

    assert result_dict["success"] is True
    assert result_dict["iterations"] == 3
    assert result_dict["cost_usd"] == 0.5
    assert len(result_dict["reasoning_trace"]) == 2
