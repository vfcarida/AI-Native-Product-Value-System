"""
Build agent integration tests (P012).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.agents.build_agent import BuildAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.runtime.sandbox import SandboxConfig


@dataclass
class MockExecutionResult:
    """Mock sandbox command execution result."""

    exit_code: int = 0
    stdout: str = "Tests passed"
    stderr: str = ""
    duration_seconds: float = 0.05


class MockSandboxProvider:
    """Mock sandbox provider recording created/destroyed sandboxes and commands."""

    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.created_configs: list[SandboxConfig] = []
        self.destroyed_sandboxes: list[str] = []
        self.executed_commands: list[str] = []

    async def create(self, config: SandboxConfig) -> str:
        self.created_configs.append(config)
        return "mock-sandbox-001"

    async def execute(
        self,
        sandbox: Any,
        command: str,
        timeout_seconds: int | None = None,
    ) -> MockExecutionResult:
        self.executed_commands.append(command)
        return MockExecutionResult(exit_code=self.exit_code)

    async def destroy(self, sandbox: Any) -> None:
        self.destroyed_sandboxes.append(str(sandbox))


@pytest.fixture
def schema_validator() -> SchemaValidator:
    """Schema validator."""
    schema_dir = Path(__file__).parent.parent.parent / "schemas"
    return SchemaValidator(schema_dir)


@pytest.fixture
def sandbox_provider() -> MockSandboxProvider:
    """Sandbox provider for build agent tests."""
    return MockSandboxProvider()


@pytest.fixture
def sample_specification() -> dict[str, Any]:
    """Sample specification for build."""
    now = datetime.now(UTC).isoformat()
    return {
        "id": "spec-calculator-001",
        "version": 1,
        "created": now,
        "updated": now,
        "outcome_id": "outcome-calculator-001",
        "functional_requirements": [
            {
                "id": "FR-001",
                "description": "Calculator shall add two numbers",
                "priority": "must-have",
                "acceptance_criteria": [
                    "Function accepts two numbers",
                    "Returns sum of numbers",
                    "Handles negative numbers",
                ],
                "linked_to": ["primary_metric"],
            }
        ],
        "architecture_decisions": [],
        "quality_attributes": {
            "performance": {},
            "security": [],
            "scalability": [],
            "reliability": [],
        },
        "testability_criteria": {
            "unit_tests": [
                "Test addition with positive numbers",
                "Test with negative numbers",
            ],
            "integration_tests": [],
            "acceptance_tests": [],
        },
        "constraints": {
            "technical": ["Python 3.12+"],
            "operational": [],
            "business": [],
        },
    }


@pytest.fixture
def mock_implementation_response() -> str:
    """Mock implementation code response."""
    return '''```python
def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b
```'''


@pytest.fixture
def mock_test_response() -> str:
    """Mock test code response."""
    return """```python
import pytest
from fr_001 import add

def test_add_positive_numbers():
    assert add(2, 3) == 5

def test_add_negative_numbers():
    assert add(-2, -3) == -5

def test_add_mixed_numbers():
    assert add(-2, 5) == 3
```"""


@pytest.mark.asyncio
@pytest.mark.integration
async def test_build_agent_creates_sandbox(
    schema_validator: SchemaValidator,
    sandbox_provider: MockSandboxProvider,
    sample_specification: dict[str, Any],
) -> None:
    """Test build agent creates and destroys sandbox with resource limits."""
    agent = BuildAgent(schema_validator, sandbox_provider)

    model_adapter = MockModelAdapter(
        responses=[
            "def add(a, b): return a + b",
            "def test_add(): assert add(1, 2) == 3",
        ]
    )
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-001")

    result = await agent.execute(sample_specification, harness, context)

    assert result["status"] == "success"
    assert len(sandbox_provider.created_configs) == 1
    assert sandbox_provider.created_configs[0].cpu_limit == 2.0
    assert sandbox_provider.created_configs[0].memory_limit_mb == 4096
    assert sandbox_provider.destroyed_sandboxes == ["mock-sandbox-001"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_build_agent_generates_code_and_tests(
    schema_validator: SchemaValidator,
    sample_specification: dict[str, Any],
    mock_implementation_response: str,
    mock_test_response: str,
) -> None:
    """Test build agent generates implementation, tests, and commits incrementally."""
    provider = MockSandboxProvider()
    agent = BuildAgent(schema_validator, provider)

    model_adapter = MockModelAdapter(
        responses=[mock_implementation_response, mock_test_response]
    )
    harness = Harness(model_adapter=model_adapter)
    context = ExecutionContext(run_id="test-run-002")

    result = await agent.execute(sample_specification, harness, context)

    assert result is not None
    assert result["specification_id"] == "spec-calculator-001"
    assert result["requirements_implemented"] == 1
    assert result["requirements_total"] == 1
    assert result["build_results"][0]["implementation_file"] == "fr_001.py"
    assert result["build_results"][0]["test_file"] == "test_fr_001.py"
    assert any("git commit" in cmd for cmd in provider.executed_commands)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_build_agent_validates_specification(
    schema_validator: SchemaValidator,
) -> None:
    """Test build agent validates input specification."""
    provider = MockSandboxProvider()
    agent = BuildAgent(schema_validator, provider)

    invalid_spec = {"id": "bad"}

    harness = Harness(model_adapter=MockModelAdapter())
    context = ExecutionContext(run_id="test-run-003")

    with pytest.raises(jsonschema.ValidationError):
        await agent.execute(invalid_spec, harness, context)
