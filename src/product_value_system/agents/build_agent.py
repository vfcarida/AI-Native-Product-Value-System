"""
Build agent: Specification -> Code + Tests (in sandbox).
"""

import json
from datetime import UTC, datetime
from typing import Any

from product_value_system.agents.base_agent import BaseAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.tracing import traced
from product_value_system.runtime.sandbox import SandboxConfig, SandboxProvider


class BuildAgent(BaseAgent):
    """
    Build agent generates code and tests from specifications.

    Process:
    1. Load specification
    2. Create Docker sandbox
    3. For each functional requirement:
       a. Generate implementation code
       b. Generate tests
       c. Execute tests in sandbox
       d. If tests pass, commit incrementally
       e. If tests fail, iterate (up to max_iterations)
    4. Destroy sandbox
    5. Return build result with artifacts
    """

    def __init__(
        self,
        schema_validator: SchemaValidator,
        sandbox_provider: SandboxProvider | Any,
    ) -> None:
        """
        Initialize build agent.

        Args:
            schema_validator: Schema validator for specifications
            sandbox_provider: Sandbox provider (DockerSandboxProvider or compatible)
        """
        super().__init__(
            name="build-agent",
            version="0.1.0",
            capabilities=["code_generation", "test_generation", "sandbox_execution"],
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

    @traced("build_agent.execute")
    async def execute(  # type: ignore[override]
        self,
        specification_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> dict[str, Any]:
        """
        Execute build process.

        Args:
            specification_artifact: Validated specification.yaml content
            harness: Harness providing model, tools, memory
            context: Execution context (run_id, budgets, etc.)

        Returns:
            Build result with code artifacts, test results, commits
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

            build_summary = self._create_build_summary(
                specification_artifact,
                build_results,
                final_test_result,
            )

            return build_summary

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

    async def _implement_requirement(
        self,
        requirement: dict[str, Any],
        specification: dict[str, Any],
        sandbox: Any,
        harness: Harness,
        context: ExecutionContext,
    ) -> dict[str, Any]:
        """Implement a single requirement (code + tests)."""
        req_id = str(requirement["id"])

        impl_prompt = self._build_implementation_prompt(requirement, specification)
        impl_response = await harness.invoke_model(
            messages=[
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": impl_prompt},
            ],
            temperature=0.2,
            max_tokens=2000,
        )

        implementation_code = self._extract_code(impl_response.content)

        test_prompt = self._build_test_prompt(requirement, implementation_code)
        test_response = await harness.invoke_model(
            messages=[
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": test_prompt},
            ],
            temperature=0.2,
            max_tokens=2000,
        )

        test_code = self._extract_code(test_response.content)

        impl_filename = self._get_implementation_filename(req_id)
        test_filename = self._get_test_filename(req_id)

        await self.sandbox_provider.execute(
            sandbox,
            f"""
            cat > /workspace/src/{impl_filename} << 'EOF'
{implementation_code}
EOF
            cat > /workspace/tests/{test_filename} << 'EOF'
{test_code}
EOF
            """,
            timeout_seconds=10,
        )

        test_result = await self._run_tests(sandbox, test_filename)

        if test_result["passed"]:
            await self._commit(
                sandbox,
                f"Implement {req_id}: {requirement['description']}",
                req_id,
            )

        return {
            "requirement_id": req_id,
            "implementation_file": impl_filename,
            "test_file": test_filename,
            "test_result": test_result,
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

    def _get_implementation_filename(self, req_id: str) -> str:
        """Get implementation filename for requirement."""
        return f"{req_id.lower().replace('-', '_')}.py"

    def _get_test_filename(self, req_id: str) -> str:
        """Get test filename for requirement."""
        return f"test_{req_id.lower().replace('-', '_')}.py"

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
        """Create Git commit in sandbox."""
        await self.sandbox_provider.execute(
            sandbox,
            f"""
            cd /workspace
            git add .
            git commit -m "{message}

Requirement: {req_id}
Agent: {self.name} v{self.version}"
            """,
            timeout_seconds=10,
        )

    def _create_build_summary(
        self,
        specification: dict[str, Any],
        build_results: list[dict[str, Any]],
        final_test_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create build summary artifact."""
        now = datetime.now(UTC).isoformat()

        total_requirements = len(build_results)
        passed_requirements = sum(
            1 for r in build_results if r["test_result"]["passed"]
        )

        return {
            "id": f"{specification['id']}-build",
            "version": 1,
            "created": now,
            "specification_id": specification["id"],
            "status": "success" if final_test_result["passed"] else "partial",
            "requirements_implemented": passed_requirements,
            "requirements_total": total_requirements,
            "build_results": build_results,
            "final_test_result": final_test_result,
            "agent": f"{self.name} v{self.version}",
        }
