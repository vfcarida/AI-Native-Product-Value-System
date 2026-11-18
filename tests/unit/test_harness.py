"""
Unit tests for harness components (P007).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from product_value_system.harness.budget import Budget, BudgetExceededError
from product_value_system.harness.checkpoint import Checkpoint, CheckpointManager
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.guardrails import Guardrails, GuardrailViolation
from product_value_system.harness.harness import Harness
from product_value_system.harness.memory import Memory
from product_value_system.policy.engine import PolicyEngine


class DummyTool:
    """Simple tool implementation for testing ToolRegistry."""

    @property
    def name(self) -> str:
        return "knowledge_base"

    @property
    def description(self) -> str:
        return "Query knowledge base"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"query": {"type": "string"}}}

    async def execute(self, **kwargs: Any) -> Any:
        return {"results": [f"found: {kwargs.get('query')}"]}


class ExternalApiTool:
    """External API tool requiring human approval."""

    @property
    def name(self) -> str:
        return "external_api"

    @property
    def description(self) -> str:
        return "Call external API"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        return {"status": "ok"}


@pytest.mark.unit
def test_budget_cost_exceeded() -> None:
    """Test budget cost limit."""
    budget = Budget(max_cost_usd=Decimal("1.0"))
    budget.start()

    budget.record_cost(Decimal("0.5"))
    assert budget.check_cost()

    budget.record_cost(Decimal("0.6"))
    assert not budget.check_cost()


@pytest.mark.unit
def test_budget_iterations_exceeded() -> None:
    """Test budget iteration limit."""
    budget = Budget(max_iterations=3)
    budget.start()

    for _ in range(3):
        budget.record_iteration()
        assert budget.check_iterations()

    budget.record_iteration()
    assert not budget.check_iterations()


@pytest.mark.unit
def test_budget_duration_exceeded() -> None:
    """Test budget time limit."""
    budget = Budget(max_duration_seconds=2)
    budget.start()

    assert budget.check_duration()

    budget.start_time = datetime.now(UTC) - timedelta(seconds=3)
    assert not budget.check_duration()


@pytest.mark.unit
def test_budget_check_all() -> None:
    """Test comprehensive budget check."""
    budget = Budget(
        max_cost_usd=Decimal("1.0"),
        max_iterations=5,
        max_duration_seconds=10,
    )
    budget.start()

    passed, error = budget.check_all()
    assert passed
    assert error is None

    rem = budget.remaining()
    assert rem["cost_usd"] == 1.0
    assert rem["iterations"] == 5

    budget.record_cost(Decimal("1.5"))
    passed, error = budget.check_all()
    assert not passed
    assert error is not None
    assert "Cost budget exceeded" in error


@pytest.mark.unit
def test_memory_add_message() -> None:
    """Test adding messages to memory."""
    memory = Memory(max_messages=3)

    memory.add_message("system", "You are helpful")
    memory.add_message("user", "Hello")
    memory.add_message("assistant", "Hi there")

    messages = memory.get_messages()
    assert len(messages) == 3


@pytest.mark.unit
def test_memory_trim() -> None:
    """Test memory trimming."""
    memory = Memory(max_messages=3)

    memory.add_message("system", "System prompt")
    memory.add_message("user", "Message 1")
    memory.add_message("assistant", "Reply 1")
    memory.add_message("user", "Message 2")

    messages = memory.get_messages()
    assert len(messages) == 3
    assert any(m.role == "system" for m in messages)


@pytest.mark.unit
def test_memory_artifacts() -> None:
    """Test artifact storage."""
    memory = Memory()

    memory.store_artifact("spec", {"title": "Specification"})
    memory.store_artifact("problem", {"id": "problem-1"})

    assert memory.retrieve_artifact("spec") == {"title": "Specification"}
    assert "problem" in memory.list_artifacts()


@pytest.mark.unit
def test_guardrails_iteration_limit() -> None:
    """Test iteration limit guardrail."""
    guardrails = Guardrails(max_iterations=3)

    passed, error = guardrails.check_iteration_limit(2)
    assert passed

    passed, error = guardrails.check_iteration_limit(4)
    assert not passed
    assert error is not None
    assert "exceeded" in error


@pytest.mark.unit
def test_guardrails_blocked_patterns() -> None:
    """Test content filtering."""
    guardrails = Guardrails(blocked_patterns=[r"password:\s*\w+"])

    passed, _ = guardrails.check_content("Hello world")
    assert passed

    passed, _ = guardrails.check_content("password: secret123")
    assert not passed


@pytest.mark.unit
def test_checkpoint_save_load(tmp_path: Path) -> None:
    """Test checkpoint persistence."""
    mgr = CheckpointManager(tmp_path)

    checkpoint = Checkpoint(
        run_id="run-123",
        stage_name="discovery",
        agent_name="discovery-agent",
        iteration=5,
        timestamp=datetime.now(UTC),
        state={"progress": 50},
    )

    path = mgr.save(checkpoint)
    assert path.exists()

    loaded = mgr.load("run-123", "discovery")
    assert loaded is not None
    assert loaded.iteration == 5
    assert loaded.state["progress"] == 50


@pytest.mark.unit
def test_checkpoint_delete(tmp_path: Path) -> None:
    """Test checkpoint deletion."""
    mgr = CheckpointManager(tmp_path)

    checkpoint = Checkpoint(
        run_id="run-456",
        stage_name="spec",
        agent_name="spec-agent",
        iteration=1,
        timestamp=datetime.now(UTC),
    )

    mgr.save(checkpoint)

    deleted = mgr.delete("run-456", "spec")
    assert deleted

    loaded = mgr.load("run-456", "spec")
    assert loaded is None


@pytest.mark.unit
def test_execution_context() -> None:
    """Test execution context."""
    ctx = ExecutionContext(
        run_id="run-789",
        stage_name="build",
        agent_name="build-agent",
        system_prompt="You are a builder",
        allowed_tools=["git", "file_write"],
    )

    assert ctx.run_id == "run-789"
    assert "git" in ctx.allowed_tools

    ctx_dict = ctx.to_dict()
    assert ctx_dict["agent_name"] == "build-agent"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_harness_constraints_kill_switch_and_tools(tmp_path: Path) -> None:
    """Test Harness constraint checks, kill switch, and policy-gated tool execution."""
    ctx = ExecutionContext(
        run_id="run-999",
        stage_name="discovery",
        agent_name="discovery-agent",
        system_prompt="Analyze problem",
    )
    policy_engine = PolicyEngine(Path(__file__).parent.parent.parent / "policies")
    budget = Budget(max_cost_usd=Decimal("1.0"), max_iterations=2)
    guardrails = Guardrails(max_iterations=2)

    harness = Harness(
        context=ctx,
        policy_engine=policy_engine,
        budget=budget,
        guardrails=guardrails,
        checkpoint_dir=tmp_path,
    )
    harness.budget.start()
    harness.tools.register(DummyTool())
    harness.tools.register(ExternalApiTool())

    harness.check_constraints(1)
    cp_path = harness.save_checkpoint(1, {"step": 1})
    assert cp_path is not None and cp_path.exists()
    loaded = harness.load_checkpoint()
    assert loaded is not None and loaded.state["step"] == 1

    # Authorized tool call
    allow_call = await harness.tools.execute(
        agent_name="discovery-agent",
        tool_name="knowledge_base",
        parameters={"query": "fees"},
        context={"environment": "sandbox"},
    )
    assert allow_call.authorized is True
    assert allow_call.result == {"results": ["found: fees"]}

    # Unauthorized tool call (external_api without human approval)
    deny_call = await harness.tools.execute(
        agent_name="discovery-agent",
        tool_name="external_api",
        parameters={},
        context={"environment": "sandbox", "has_human_approval": False},
    )
    assert deny_call.authorized is False
    assert deny_call.error is not None

    # Guardrail iteration limit
    with pytest.raises(GuardrailViolation):
        harness.check_constraints(3)

    # Budget exceeded
    harness.budget.record_cost(Decimal("2.0"))
    with pytest.raises(BudgetExceededError):
        harness.check_constraints(1)

    # Kill switch
    harness.kill()
    assert harness.is_killed()
    with pytest.raises(RuntimeError, match="kill switch"):
        harness.check_constraints(1)
