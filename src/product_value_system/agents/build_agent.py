"""
Build agent: Specification -> Iterative ReAct Code + Tests + Structured Diffs
(in isolated sandbox) (EVO-P3-09).
"""

from __future__ import annotations

import base64
import difflib
import json
import re
import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from product_value_system.adapters.model_adapter import Message, Role
from product_value_system.agents.base_agent import BaseAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.tracing import traced
from product_value_system.runtime.sandbox import SandboxConfig, SandboxProvider


@dataclass(frozen=True)
class FileDiff:
    """Structured unified diff produced by an agent code edit."""

    path: str
    change_type: str  # "created", "modified", "unchanged"
    unified_diff: str
    lines_added: int
    lines_removed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "unified_diff": self.unified_diff,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
        }


@dataclass(frozen=True)
class BuildIterationRecord:
    """Telemetry record for a single ReAct build -> test -> observe -> repair iteration."""

    iteration: int
    thought: str
    action: str
    files_modified: list[str]
    tests_passed: bool
    test_output_excerpt: str
    diffs: list[FileDiff] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "thought": self.thought,
            "action": self.action,
            "files_modified": list(self.files_modified),
            "tests_passed": self.tests_passed,
            "test_output_excerpt": self.test_output_excerpt,
            "diffs": [d.to_dict() for d in self.diffs],
        }


def compute_file_diff(path: str, before: str, after: str) -> FileDiff:
    """Compute a structured unified diff and line delta between two file revisions."""
    if before == after:
        return FileDiff(
            path=path,
            change_type="unchanged",
            unified_diff="",
            lines_added=0,
            lines_removed=0,
        )

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
    return FileDiff(
        path=path,
        change_type="created" if not before else "modified",
        unified_diff="".join(diff_lines),
        lines_added=added,
        lines_removed=removed,
    )


class BuildAgent(BaseAgent):
    """
    Build agent generates, tests, and iteratively repairs code and tests in a
    sandbox using a ReAct (Reason -> Patch -> Execute -> Observe) loop with
    structured unified diff artifacts.
    """

    def __init__(
        self,
        schema_validator: SchemaValidator,
        sandbox_provider: SandboxProvider | Any,
        *,
        max_iterations: int = 3,
    ) -> None:
        """
        Initialize build agent.

        Args:
            schema_validator: Schema validator for specifications
            sandbox_provider: Sandbox provider (DockerSandboxProvider or compatible)
            max_iterations: Maximum ReAct build/repair iterations per requirement
        """
        super().__init__(
            name="build-agent",
            version="1.2.0",
            capabilities=[
                "code_generation",
                "test_generation",
                "sandbox_execution",
                "react_iterative_repair",
                "structured_diffs",
            ],
            required_tools=[
                "sandbox.execute",
                "sandbox.copy_in",
                "sandbox.copy_out",
                "git.commit",
                "test.run",
            ],
            input_schema="https://pvs.ai/schemas/specification.schema.json",
            output_schema="https://pvs.ai/schemas/evidence.schema.json",
        )
        self.schema_validator = schema_validator
        self.sandbox_provider = sandbox_provider
        self.max_iterations = max(1, int(max_iterations))

    @traced("build_agent.execute")
    async def execute(  # type: ignore[override]
        self,
        specification_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> dict[str, Any]:
        """
        Execute iterative build process across all functional requirements.

        Args:
            specification_artifact: Validated specification.yaml content
            harness: Harness providing model, tools, memory, budget & guardrails
            context: Execution context (run_id, budgets, etc.)

        Returns:
            Build result with code artifacts, structured diffs, iteration logs,
            test results, and incremental Git commits
        """
        self.schema_validator.validate(specification_artifact, "specification.schema.json")

        sandbox_config = SandboxConfig(
            image="pvs/agent-sandbox:latest",
            cpu_limit=2.0,
            memory_limit_mb=4096,
            disk_limit_mb=10240,
            timeout_seconds=900,
            network_enabled=False,
            network_mode="restricted",
        )

        sandbox = await self.sandbox_provider.create(sandbox_config)

        try:
            await self._init_workspace(sandbox, specification_artifact)

            build_results: list[dict[str, Any]] = []
            requirements = specification_artifact["functional_requirements"]

            for req in requirements:
                result = await self._implement_requirement(
                    req,
                    specification_artifact,
                    sandbox,
                    harness,
                    context,
                )
                build_results.append(result)

            final_test_result = await self._run_all_tests(sandbox)

            return self._create_build_summary(
                specification_artifact,
                build_results,
                final_test_result,
            )

        finally:
            await self.sandbox_provider.destroy(sandbox)

    async def _init_workspace(
        self,
        sandbox: Any,
        specification: dict[str, Any],
    ) -> None:
        """Initialize sandbox workspace with project structure."""
        await self.sandbox_provider.execute(
            sandbox,
            """
            mkdir -p /workspace/src /workspace/tests
            cd /workspace
            git init
            git config user.name "Build Agent"
            git config user.email "build@pvs.ai"
            """,
            timeout_seconds=30,
        )

    async def _write_sandbox_files(
        self,
        sandbox: Any,
        *,
        impl_filename: str,
        implementation_code: str,
        test_filename: str,
        test_code: str,
    ) -> None:
        """Write implementation and test files cleanly into the sandbox via base64 encoding."""
        impl_b64 = base64.b64encode(implementation_code.encode("utf-8")).decode("ascii")
        test_b64 = base64.b64encode(test_code.encode("utf-8")).decode("ascii")
        impl_path = shlex.quote(f"/workspace/src/{impl_filename}")
        test_path = shlex.quote(f"/workspace/tests/{test_filename}")
        await self.sandbox_provider.execute(
            sandbox,
            (
                f"printf '%s' {shlex.quote(impl_b64)} | base64 -d > {impl_path} && "
                f"printf '%s' {shlex.quote(test_b64)} | base64 -d > {test_path}"
            ),
            timeout_seconds=10,
        )

    async def _implement_requirement(
        self,
        requirement: dict[str, Any],
        specification: dict[str, Any],
        sandbox: Any,
        harness: Harness,
        context: ExecutionContext,
    ) -> dict[str, Any]:
        """Implement a single requirement using an iterative ReAct build-test-fix loop."""
        req_id = str(requirement["id"])
        impl_filename = self._get_implementation_filename(req_id)
        test_filename = self._get_test_filename(req_id)
        system_msg = Message(
            role=Role.SYSTEM,
            content=self._build_system_prompt(),
            cache_control=True,
        )

        # Iteration 1: Initial implementation & test authoring
        impl_prompt = self._build_implementation_prompt(requirement, specification)
        impl_response = await harness.invoke_model(
            messages=[system_msg, Message(role=Role.USER, content=impl_prompt)],
            temperature=0.2,
            max_tokens=2000,
        )
        implementation_code = self._extract_code(impl_response.content)

        test_prompt = self._build_test_prompt(requirement, implementation_code)
        test_response = await harness.invoke_model(
            messages=[system_msg, Message(role=Role.USER, content=test_prompt)],
            temperature=0.2,
            max_tokens=2000,
        )
        test_code = self._extract_code(test_response.content)

        prev_impl = ""
        prev_test = ""
        iterations: list[BuildIterationRecord] = []
        all_diffs: list[FileDiff] = []

        test_result: dict[str, Any] = {"passed": False, "output": "", "errors": ""}

        for attempt in range(1, self.max_iterations + 1):
            impl_diff = compute_file_diff(f"src/{impl_filename}", prev_impl, implementation_code)
            test_diff = compute_file_diff(f"tests/{test_filename}", prev_test, test_code)
            step_diffs = [d for d in (impl_diff, test_diff) if d.change_type != "unchanged"]
            all_diffs.extend(step_diffs)

            await self._write_sandbox_files(
                sandbox,
                impl_filename=impl_filename,
                implementation_code=implementation_code,
                test_filename=test_filename,
                test_code=test_code,
            )

            test_result = await self._run_tests(sandbox, test_filename)
            output_excerpt = (
                f"{test_result.get('output', '')}\n{test_result.get('errors', '')}"
            ).strip()[:1200]

            step_record = BuildIterationRecord(
                iteration=attempt,
                thought=(
                    f"Initial implementation and tests for {req_id}"
                    if attempt == 1
                    else f"Repairing {req_id} based on failing sandbox pytest diagnostics"
                ),
                action="generate_and_test" if attempt == 1 else "patch_and_retest",
                files_modified=[d.path for d in step_diffs],
                tests_passed=bool(test_result["passed"]),
                test_output_excerpt=output_excerpt,
                diffs=step_diffs,
            )
            iterations.append(step_record)

            if harness.trajectory_capture.enabled:
                harness.trajectory_capture.log_step(
                    thought=step_record.thought,
                    action={
                        "tool": "sandbox.pytest",
                        "parameters": {"req_id": req_id, "iteration": attempt},
                    },
                    observation="PASSED" if step_record.tests_passed else output_excerpt[:300],
                    latency_ms=0.0,
                    policy_checked=True,
                    policy_decision="ALLOW",
                )

            if test_result["passed"]:
                await self._commit(
                    sandbox,
                    f"Implement {req_id}: {requirement['description']}",
                    req_id,
                )
                break

            if attempt >= self.max_iterations or harness.is_killed():
                break

            # ReAct Repair step: feed exact pytest failure back to the model
            prev_impl = implementation_code
            prev_test = test_code
            repair_prompt = self._build_repair_prompt(
                requirement=requirement,
                implementation_code=implementation_code,
                test_code=test_code,
                test_output=output_excerpt,
                attempt=attempt + 1,
            )
            repair_response = await harness.invoke_model(
                messages=[system_msg, Message(role=Role.USER, content=repair_prompt)],
                temperature=0.1,
                max_tokens=2000,
            )
            repaired_impl, repaired_test = self._extract_repair_blocks(repair_response.content)
            if repaired_impl:
                implementation_code = repaired_impl
            if repaired_test:
                test_code = repaired_test

        return {
            "requirement_id": req_id,
            "implementation_file": impl_filename,
            "test_file": test_filename,
            "test_result": test_result,
            "attempts": len(iterations),
            "iterations": [rec.to_dict() for rec in iterations],
            "diffs": [d.to_dict() for d in all_diffs],
        }

    def _build_system_prompt(self) -> str:
        """Build system prompt for build agent."""
        return (
            "You are a code generation agent specializing in Python implementation.\n\n"
            "Your role:\n"
            "- Generate clean, working Python code from specifications\n"
            "- Write comprehensive tests with pytest\n"
            "- Follow best practices (PEP 8, type hints, docstrings)\n"
            "- Keep code simple and focused on requirements\n\n"
            "Code quality standards:\n"
            "- Type hints for all function signatures\n"
            "- Docstrings for all public functions\n"
            "- No hardcoded values (use configuration)\n"
            "- Error handling for edge cases\n"
            "- Tests cover happy path and edge cases\n\n"
            "Be CONCISE and CORRECT. Focus on functionality, not optimization."
        )

    def _build_implementation_prompt(
        self,
        requirement: dict[str, Any],
        specification: dict[str, Any],
    ) -> str:
        """Build prompt for implementing a requirement."""
        acceptance = "\n".join(requirement["acceptance_criteria"])
        tech_constraints = specification.get("constraints", {}).get("technical", [])

        return f"""Implement this functional requirement in Python:

## Requirement
{requirement['id']}: {requirement['description']}

## Acceptance Criteria
{acceptance}

## Quality Attributes
{json.dumps(specification.get('quality_attributes', {}), indent=2)}

## Constraints
{json.dumps(tech_constraints, indent=2)}

Generate a Python module with:
- Function(s) implementing the requirement
- Type hints
- Docstrings
- Error handling

Respond with Python code only (in ```python block)."""

    def _build_test_prompt(
        self,
        requirement: dict[str, Any],
        implementation_code: str,
    ) -> str:
        """Build prompt for generating tests."""
        acceptance = "\n".join(requirement["acceptance_criteria"])

        return f"""Write pytest tests for this implementation:

## Requirement
{requirement['id']}: {requirement['description']}

## Acceptance Criteria
{acceptance}

## Implementation
```python
{implementation_code}
```

Generate pytest tests with:
- Test for each acceptance criterion
- Happy path tests
- Edge case tests
- Error condition tests

Respond with Python test code only (in ```python block)."""

    def _build_repair_prompt(
        self,
        *,
        requirement: dict[str, Any],
        implementation_code: str,
        test_code: str,
        test_output: str,
        attempt: int,
    ) -> str:
        """Build targeted ReAct repair prompt containing failing sandbox pytest output."""
        acceptance = "\n".join(requirement["acceptance_criteria"])
        return f"""Fix the failing implementation (ReAct repair iteration {attempt}):

## Requirement
{requirement['id']}: {requirement['description']}

## Acceptance Criteria
{acceptance}

## Current Implementation
```python
{implementation_code}
```

## Current Tests
```python
{test_code}
```

## Sandbox Pytest Failure Output
```text
{test_output}
```

Analyze the root cause and return the corrected full Python implementation in a ```python block.
If tests also need updates, add a second ```python block after `# --- TESTS ---`."""

    def _extract_code(self, response: str) -> str:
        """Extract code from model response."""
        content = response.strip()
        if "```python" in content:
            code = content.split("```python")[1].split("```")[0].strip()
        elif "```" in content:
            code = content.split("```")[1].split("```")[0].strip()
        else:
            code = content
        return code

    def _extract_repair_blocks(self, response: str) -> tuple[str, str | None]:
        """Extract patched implementation and optional test patch from repair response."""
        marker = "# --- TESTS ---"
        if marker in response:
            impl_part, test_part = response.split(marker, 1)
            return self._extract_code(impl_part), self._extract_code(test_part)
        return self._extract_code(response), None

    @staticmethod
    def _sanitize_req_id(req_id: str) -> str:
        """Sanitize requirement ID to a safe lowercase identifier (no path traversal)."""
        cleaned = re.sub(r"[^a-z0-9_]", "_", req_id.lower())
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        return cleaned or "req"

    def _get_implementation_filename(self, req_id: str) -> str:
        """Get implementation filename for requirement."""
        return f"{self._sanitize_req_id(req_id)}.py"

    def _get_test_filename(self, req_id: str) -> str:
        """Get test filename for requirement."""
        return f"test_{self._sanitize_req_id(req_id)}.py"

    async def _run_tests(self, sandbox: Any, test_filename: str) -> dict[str, Any]:
        """Run tests in sandbox."""
        result = await self.sandbox_provider.execute(
            sandbox,
            f"cd /workspace && PYTHONPATH=src python -m pytest tests/{test_filename} -v",
            timeout_seconds=60,
        )

        return {
            "passed": result.exit_code == 0,
            "output": result.stdout,
            "errors": result.stderr,
        }

    async def _run_all_tests(self, sandbox: Any) -> dict[str, Any]:
        """Run all tests in sandbox."""
        result = await self.sandbox_provider.execute(
            sandbox,
            "cd /workspace && PYTHONPATH=src python -m pytest tests/ -v",
            timeout_seconds=120,
        )

        return {
            "passed": result.exit_code == 0,
            "output": result.stdout,
            "errors": result.stderr,
        }

    async def _commit(self, sandbox: Any, message: str, req_id: str) -> None:
        """Create Git commit in sandbox safely escaping commit message."""
        full_msg = f"{message}\n\nRequirement: {req_id}\nAgent: {self.name} v{self.version}"
        await self.sandbox_provider.execute(
            sandbox,
            f"cd /workspace && git add . && git commit -m {shlex.quote(full_msg)}",
            timeout_seconds=10,
        )

    def _create_build_summary(
        self,
        specification: dict[str, Any],
        build_results: list[dict[str, Any]],
        final_test_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create build summary artifact with ReAct iteration counts and structured diffs."""
        now = datetime.now(UTC).isoformat()

        total_requirements = len(build_results)
        passed_requirements = sum(
            1 for r in build_results if r["test_result"]["passed"]
        )
        total_iterations = sum(int(r.get("attempts", 1)) for r in build_results)
        all_diffs = [
            diff
            for r in build_results
            for diff in r.get("diffs", [])
        ]

        return {
            "id": f"{specification['id']}-build",
            "version": 1,
            "created": now,
            "specification_id": specification["id"],
            "status": "success" if final_test_result["passed"] else "partial",
            "requirements_implemented": passed_requirements,
            "requirements_total": total_requirements,
            "total_iterations": total_iterations,
            "diffs": all_diffs,
            "build_results": build_results,
            "final_test_result": final_test_result,
            "agent": f"{self.name} v{self.version}",
        }
