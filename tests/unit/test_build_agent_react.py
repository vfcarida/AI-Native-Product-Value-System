"""
Tests for BuildAgent iterative ReAct (write -> test -> observe -> repair)
sandbox loop, structured unified diffs, trajectory capture, and safe commit
escaping (EVO-P3-09).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.agents.build_agent import BuildAgent, compute_file_diff
from product_value_system.evaluation.trajectory_capture import TrajectoryCapture
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness


class _NoOpSchemaValidator:
    def validate(self, _data: dict[str, Any], _schema_name: str) -> None:
        return None


@dataclass
class _ExecResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class _SimulatedSandboxProvider:
    """Stateful mock sandbox simulating test failure -> repair -> green pytest."""

    def __init__(self, *, fail_times: int = 1) -> None:
        self.fail_times = fail_times
        self.pytest_runs = 0
        self.commands: list[str] = []
        self.commits: list[str] = []
        self.destroyed = False

    async def create(self, _config: Any) -> dict[str, str]:
        return {"id": "sandbox-react-1"}

    async def execute(
        self,
        _sandbox: Any,
        command: str,
        timeout_seconds: int = 30,  # noqa: ARG002
    ) -> _ExecResult:
        self.commands.append(command)
        if "git commit" in command:
            self.commits.append(command)
            return _ExecResult(exit_code=0, stdout="[main abc123] committed")
        if "pytest tests/test_" in command:
            self.pytest_runs += 1
            if self.pytest_runs <= self.fail_times:
                return _ExecResult(
                    exit_code=1,
                    stdout="FAILED tests/test_fr_001.py::test_discount - AssertionError: 90 != 80",
                    stderr="",
                )
            return _ExecResult(exit_code=0, stdout="2 passed in 0.04s")
        if "pytest tests/" in command:
            passed = self.pytest_runs > self.fail_times
            return _ExecResult(
                exit_code=0 if passed else 1,
                stdout="2 passed" if passed else "1 failed",
            )
        return _ExecResult(exit_code=0, stdout="ok")

    async def destroy(self, _sandbox: Any) -> None:
        self.destroyed = True


def _sample_spec() -> dict[str, Any]:
    return {
        "id": "SPEC-REACT-001",
        "functional_requirements": [
            {
                "id": "FR-001",
                "description": 'Apply 20% "VIP" promo code discount safely',
                "acceptance_criteria": [
                    "Given price 100 and VIP code, returns 80.0",
                    "Rejects negative prices with ValueError",
                ],
            }
        ],
        "quality_attributes": {"latency_p95_ms": 50},
        "constraints": {"technical": ["Python 3.12+"]},
    }


def test_compute_file_diff_tracks_created_modified_and_unchanged() -> None:
    created = compute_file_diff("src/app.py", "", "def f() -> int:\n    return 1\n")
    assert created.change_type == "created"
    assert created.lines_added == 2
    assert created.lines_removed == 0
    assert "+def f() -> int:" in created.unified_diff

    modified = compute_file_diff(
        "src/app.py",
        "def f() -> int:\n    return 1\n",
        "def f() -> int:\n    return 2\n",
    )
    assert modified.change_type == "modified"
    assert modified.lines_added == 1
    assert modified.lines_removed == 1
    assert "-    return 1" in modified.unified_diff
    assert "+    return 2" in modified.unified_diff

    unchanged = compute_file_diff("src/app.py", "x = 1\n", "x = 1\n")
    assert unchanged.change_type == "unchanged"
    assert unchanged.unified_diff == ""


@pytest.mark.asyncio
async def test_build_agent_react_loop_repairs_failing_code_with_structured_diffs() -> None:
    """BuildAgent iterates on sandbox pytest failure, emits unified diffs, and commits safely."""
    sandbox = _SimulatedSandboxProvider(fail_times=1)
    mock_llm = MockModelAdapter(
        responses=[
            # 1. Initial buggy implementation (10% instead of 20%)
            "```python\ndef apply_discount(price: float) -> float:\n    return price * 0.9\n```",
            # 2. Initial tests
            "```python\nfrom fr_001 import apply_discount\n"
            "def test_discount():\n    assert apply_discount(100.0) == 80.0\n```",
            # 3. ReAct repair response fixing the bug (and optionally updating tests)
            "```python\ndef apply_discount(price: float) -> float:\n"
            "    if price < 0:\n        raise ValueError('negative')\n"
            "    return price * 0.8\n```\n# --- TESTS ---\n"
            "```python\nfrom fr_001 import apply_discount\n"
            "def test_discount():\n    assert apply_discount(100.0) == 80.0\n```",
        ]
    )
    capture = TrajectoryCapture(enabled=True)
    capture.start_trajectory("build-agent", _sample_spec())
    harness = Harness(
        context=ExecutionContext(run_id="run-react-ok", agent_name="build-agent"),
        model_adapter=mock_llm,
        trajectory_capture=capture,
    )
    agent = BuildAgent(_NoOpSchemaValidator(), sandbox, max_iterations=3)

    summary = await agent.execute(_sample_spec(), harness, harness.context)

    assert sandbox.destroyed is True
    assert summary["status"] == "success"
    assert summary["requirements_implemented"] == 1
    assert summary["total_iterations"] == 2
    assert mock_llm._call_count == 3  # impl + test + 1 repair

    req_res = summary["build_results"][0]
    assert req_res["attempts"] == 2
    assert req_res["iterations"][0]["action"] == "generate_and_test"
    assert req_res["iterations"][0]["tests_passed"] is False
    assert "AssertionError" in req_res["iterations"][0]["test_output_excerpt"]
    assert req_res["iterations"][1]["action"] == "patch_and_retest"
    assert req_res["iterations"][1]["tests_passed"] is True

    # Structured unified diff captured the repair change (-0.9 / +0.8)
    repair_diffs = req_res["iterations"][1]["diffs"]
    assert len(repair_diffs) == 1
    assert repair_diffs[0]["change_type"] == "modified"
    assert "-    return price * 0.9" in repair_diffs[0]["unified_diff"]
    assert "+    return price * 0.8" in repair_diffs[0]["unified_diff"]

    # System prompt used native prompt caching (cache_control=True)
    assert mock_llm.last_messages[0].cache_control is True

    # Git commit safely quoted description containing double quotes
    assert len(sandbox.commits) == 1
    assert "'Implement FR-001:" in sandbox.commits[0]

    # Trajectory capture recorded sandbox pytest steps
    assert capture.current_trajectory is not None
    assert len(capture.current_trajectory.steps) >= 2
    assert any(
        step.action and step.action.get("tool") == "sandbox.pytest"
        for step in capture.current_trajectory.steps
    )


@pytest.mark.asyncio
async def test_build_agent_respects_max_iterations_when_tests_keep_failing() -> None:
    """When sandbox tests keep failing, BuildAgent stops at max_iterations without committing."""
    sandbox = _SimulatedSandboxProvider(fail_times=10)
    mock_llm = MockModelAdapter(
        responses=[
            "```python\ndef f() -> int:\n    return 0\n```",
            "```python\ndef test_f():\n    assert False\n```",
            "```python\ndef f() -> int:\n    return 1\n```",
        ]
    )
    harness = Harness(
        context=ExecutionContext(run_id="run-react-cap", agent_name="build-agent"),
        model_adapter=mock_llm,
    )
    agent = BuildAgent(_NoOpSchemaValidator(), sandbox, max_iterations=2)

    summary = await agent.execute(_sample_spec(), harness, harness.context)

    assert summary["status"] == "partial"
    assert summary["requirements_implemented"] == 0
    assert summary["total_iterations"] == 2
    assert sandbox.pytest_runs == 2
    assert len(sandbox.commits) == 0
