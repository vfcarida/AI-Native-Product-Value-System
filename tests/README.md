# Testing Guide: AI-Native Product Value System

## Overview

The AI-Native Product Value System uses a **4-tier test pyramid** complemented by dedicated **Security** and **Policy Synchronization** test suites to guarantee correctness, safety, and governance at every layer:

1. **Unit Tests** (`tests/unit/` — 122 tests): Fast, isolated, deterministic (`$0`)
2. **Integration Tests** (`tests/integration/` — 44 tests): Agent, orchestrator, and CLI workflows with mocked external dependencies (`$0`)
3. **Real Integration Tests** (`tests/integration_real/` — 26 tests): Live OpenAI/Anthropic APIs, real Docker Engine sandboxes, and Cedar CLI (`~$0.33/run`, hard cap `$0.50`)
4. **Evaluation Tests** (`tests/eval/` — 7 framework tests; full curated golden dataset suite planned in EVO-011): Deterministic & AI-based quality judges
5. **Security & Policy Suites** (`tests/security/` — 95 tests; `tests/policy/` — 2 sync suites covering 25 policy cases): OWASP LLM-01 prompt injection, path/command sanitization, secret scanning, and Cedar ↔ fallback drift checks (`$0`)

---

## Test Pyramid

```
          /\
         /EV\      Eval: AI output quality & golden datasets (tests/eval/)
        /────\
       / REAL \    Real Integration: Live LLM / Docker / Cedar (tests/integration_real/)
      /────────\
     /   INT    \  Integration: Workflows with mocks (tests/integration/)
    /────────────\
   /  SEC + POL   \ Security & Policy Sync: OWASP LLM-01 + Cedar drift (tests/security/, tests/policy/)
  /────────────────\
 /       UNIT       \ Unit: Isolated, deterministic, fast (tests/unit/)
/────────────────────\
```

### When to Use Each Tier

| Tier | Location | Tests | Speed | Cost | Use When |
|------|----------|-------|-------|------|----------|
| **Unit** | `tests/unit/` | 122 | <15s | Free | Testing individual classes, parsers, adapters, budgets, schemas |
| **Integration (Mocked)** | `tests/integration/` | 44 | <30s | Free | Testing agent pipelines, orchestrator states, CLI commands, examples |
| **Security** | `tests/security/` | 95 | <10s | Free | Validating prompt injection, path/command injection, secret detection |
| **Policy Sync** | `tests/policy/` | 2 (25 cases) | <1s | Free | Verifying `agent_authorization.cedar` matches `PolicyEngine._fallback_evaluate()` |
| **Real Integration** | `tests/integration_real/` | 26 | 10–15 min | ~$0.33 (cap $0.50) | Validating live LLM APIs, Docker sandbox isolation, Cedar CLI |
| **Eval** | `tests/eval/` | 7 | <10s (framework) | Free (mocked) / ~$5 (live golden) | Testing deterministic/AI judges and golden dataset scoring |

---

## Quick Start

### Run All Unit Tests (Fast)
```bash
pytest tests/unit/ -v
# Or:
make test-unit
```

### Run Mocked Integration Tests
```bash
pytest tests/integration/ -v
# Or:
make test-integration
```

### Run Standard CI Suite (Free, All Fast Tiers)
```bash
make test
# Equivalent to:
pytest tests/unit/ tests/integration/ tests/eval/ tests/security/ tests/policy/ -v
```

### Run Security & Policy Validation Suites
```bash
pytest tests/security/ -v
pytest tests/policy/ -v
make validate-policies
```

### Run Real Integration Tests (Requires Live Credentials / Docker)
```bash
# Linux / macOS
export RUN_REAL_INTEGRATION_TESTS=1
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
make docker-build-sandbox
pytest tests/integration_real/ -v -s
# Or: make test-integration-real
```

```powershell
# Windows PowerShell
$env:RUN_REAL_INTEGRATION_TESTS = "1"
$env:OPENAI_API_KEY = "sk-..."
$env:ANTHROPIC_API_KEY = "sk-ant-..."
pytest tests/integration_real/ -v -s
```

### Run a Single Test or Class
```bash
pytest tests/unit/test_budget_guards.py::TestBudgetEnforcement::test_per_run_budget_enforced -v
```

### Run with Coverage Report
```bash
pytest tests/unit/ tests/integration/ tests/eval/ tests/security/ tests/policy/ \
  --cov=product_value_system --cov-report=term-missing
```

---

## Test Tiers in Detail

### 1. Unit Tests

**Location**: `tests/unit/`  
**Purpose**: Test individual modules, protocols, parsers, and edge cases in isolation.  
**Dependencies**: None (uses `httpx.MockTransport`, `MockModelAdapter`, `FakeDockerClient`, `tmp_path`).  
**Marker**: `@pytest.mark.unit`

**Example**:
```python
# tests/unit/test_budget_guards.py
import pytest
from product_value_system.harness.budget import Budget, BudgetExhausted


@pytest.mark.unit
class TestBudgetEnforcement:
    def test_per_run_budget_enforced(self) -> None:
        budget = Budget(max_usd=1.0)
        budget.check_before_spend(0.5)
        budget.spend(0.5)
        assert budget.spent_usd == pytest.approx(0.5)

        with pytest.raises(BudgetExhausted):
            budget.check_before_spend(0.6)
```

**Guidelines**:
- Fast (`<10ms` per test where possible)
- Zero external network calls or live Docker daemon requirements
- Use `tmp_path` for temporary SQLite databases and files
- Deterministic — identical pass/fail across OSes and CI runners

---

### 2. Integration Tests (Mocked)

**Location**: `tests/integration/`  
**Purpose**: Verify multi-component workflows (Discovery, Specification, Review, Build agents, `ValueStreamOrchestrator`, CLI subcommands, greenfield/brownfield examples).  
**Dependencies**: `MockModelAdapter`, `MockSandboxProvider`, local SQLite in `tmp_path`.  
**Marker**: `@pytest.mark.integration`

**Example**:
```python
# tests/integration/test_discovery_agent.py
from pathlib import Path
import pytest
from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.agents.discovery_agent import DiscoveryAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness


@pytest.mark.integration
@pytest.mark.asyncio
async def test_discovery_generates_outcome(sample_problem: dict, mock_outcome_response: str) -> None:
    validator = SchemaValidator(Path("schemas"))
    agent = DiscoveryAgent(schema_validator=validator)
    harness = Harness(model_adapter=MockModelAdapter(responses=[mock_outcome_response]))
    context = ExecutionContext(run_id="test-run-001")

    outcome = await agent.execute(sample_problem, harness, context)

    assert "hypothesis" in outcome
    validator.validate(outcome, "outcome.schema.json")
```

**Guidelines**:
- Exercise real agent prompts, schema validation, policy checks, and harness guardrails
- Mock only external I/O boundaries (LLM HTTP calls, Docker daemon)
- Keep total runtime under 30 seconds for rapid PR feedback

---

### 3. Real Integration Tests

**Location**: `tests/integration_real/`  
**Purpose**: Validate live end-to-end integrations with OpenAI API, Anthropic Messages API, Docker Engine (`pvs/agent-sandbox:latest`), and Cedar CLI.  
**Dependencies**: `RUN_REAL_INTEGRATION_TESTS=1`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, active Docker daemon, optional `cedar` CLI binary.  
**Marker**: `@pytest.mark.integration_real`

**Included Suites (26 tests)**:
- `test_openai_real.py` (5 tests) — `gpt-3.5-turbo` invoke, rate-limit recovery, tool calling, cost accuracy, invalid model handling
- `test_anthropic_real.py` (5 tests) — `claude-3-haiku-20240307` invoke, system prompt extraction, tool calling, cost accuracy, invalid model handling
- `test_docker_sandbox_real.py` (8 tests) — container lifecycle, execution, timeout, OOM memory limit, network isolation, file copy in/out, non-root UID, dropped capabilities
- `test_cedar_policy_real.py` (4 tests) — Cedar CLI presence, allow/deny rules, live vs. fallback consistency
- `test_end_to_end_discovery.py` (2 tests) — `DiscoveryAgent` live outcome generation and USD budget enforcement
- `test_end_to_end_build.py` (2 tests) — `BuildAgent` in real Docker + full Discovery → Spec → Build workflow

**Guidelines**:
- Always use cheapest provider models (`gpt-3.5-turbo`, `claude-3-haiku-20240307`)
- Set tight `max_tokens` (`10`–`100`) and `@pytest.mark.timeout(...)` on every test
- Always close async HTTP clients (`await adapter.close()`) and destroy Docker containers in `finally` blocks
- Always report USD spend to the session `cost_tracker` fixture
- Always gate with `RUN_REAL_INTEGRATION_TESTS=1` so standard CI skips gracefully

---

### 4. Evaluation, Security, and Policy Sync Suites

- **Evaluation Framework** (`tests/eval/test_evaluation_framework.py`): Validates `SchemaValidationJudge`, `PythonSyntaxJudge`, `TestCoverageJudge`, `CompletenessJudge`, `ClarityJudge`, `GoldenDataset`, and `EvaluationPipeline`. Full curated golden datasets are scheduled for EVO-011.
- **Security Tests** (`tests/security/`): Covers 69 OWASP LLM-01 adversarial prompt injection tests (`test_prompt_injection.py` + `injection_patterns.yaml`), path traversal (`test_path_validation.py`), command injection (`test_command_injection.py`), secret detection (`test_secret_detection.py`), and Git pre-commit hooks (`test_git_hooks.py`). See [docs/SECURITY_TESTING.md](../docs/SECURITY_TESTING.md).
- **Policy Sync Tests** (`tests/policy/test_policy_fallback_sync.py`): Wraps `scripts/validate-policy-sync.py` across 25 authorization cases and verifies intentional fallback drift is caught immediately. See [docs/POLICY_SYNC.md](../docs/POLICY_SYNC.md).

---

## Environment Variables

| Variable | Required For | Example |
|----------|--------------|---------|
| `OPENAI_API_KEY` | `OpenAIAdapter` and OpenAI real integration tests | `sk-...` |
| `ANTHROPIC_API_KEY` | `AnthropicAdapter` and Anthropic real integration tests | `sk-ant-...` |
| `ANTHROPIC_BASE_URL` | Optional Anthropic endpoint override | `https://api.anthropic.com` |
| `ANTHROPIC_DEFAULT_MODEL` | Optional default Claude model override | `claude-3-5-sonnet-20241022` |
| `RUN_REAL_INTEGRATION_TESTS` | Opt-in flag enabling `tests/integration_real/` | `1` |
| `EMERGENCY_STOP` | Global kill switch blocking all LLM calls and `pvs run` | `1` |
| `PVS_DB_PATH` | Override SQLite run persistence database path | `/tmp/pvs.db` |
| `PVS_ARTIFACT_ROOT` | Override value stream artifact output directory | `/tmp/artifacts` |

> **Security Reminder**: Never commit `.env` files or real API keys. Pre-commit secret scanning (`scripts/pre-commit-secret-scan.py`) blocks accidental credential commits.

---

## Pytest Markers

All markers are strictly enforced (`--strict-markers`) and registered in [pytest.ini](../pytest.ini):

| Marker | Description |
|--------|-------------|
| `@pytest.mark.unit` | Fast, isolated, deterministic unit tests (`$0`) |
| `@pytest.mark.integration` | Component workflow tests with mocked/local dependencies (`$0`) |
| `@pytest.mark.integration_real` | Live OpenAI/Anthropic/Docker/Cedar tests (`RUN_REAL_INTEGRATION_TESTS=1`) |
| `@pytest.mark.policy_validation` | Cedar policy vs. Python fallback synchronization tests |
| `@pytest.mark.security` | Prompt injection, path/command sanitization, and secret detection tests |
| `@pytest.mark.eval` | Evaluation framework and golden dataset quality tests |
| `@pytest.mark.slow` | Long-running tests excluded from rapid local iteration |
| `@pytest.mark.asyncio` | Async coroutine test (auto-enabled via `asyncio_mode = auto`) |
| `@pytest.mark.timeout(N)` | Hard per-test timeout guard in seconds (`pytest-timeout`) |

**Selecting Tests by Marker**:
```bash
# Run only security tests
pytest -m security -v

# Run only policy sync tests
pytest -m policy_validation -v

# Run only real integration tests
RUN_REAL_INTEGRATION_TESTS=1 pytest -m integration_real -v -s

# Exclude slow and real integration tests
pytest -m "not slow and not integration_real" -v
```

---

## Writing New Tests

### Template 1: Unit Test

```python
import pytest


@pytest.mark.unit
class TestMyComponent:
    """Unit tests for MyComponent."""

    @pytest.fixture
    def component(self) -> MyComponent:
        return MyComponent(setting="value")

    def test_expected_behavior(self, component: MyComponent) -> None:
        result = component.compute("input")
        assert result == "expected"

    def test_rejects_invalid_input(self, component: MyComponent) -> None:
        with pytest.raises(ValueError, match="invalid"):
            component.compute("")
```

### Template 2: Mocked Integration Test

```python
import pytest
from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_workflow() -> None:
    adapter = MockModelAdapter(responses=['{"status": "ok"}'])
    harness = Harness(
        context=ExecutionContext(run_id="test-workflow-001"),
        model_adapter=adapter,
    )
    response = await harness.invoke_model([{"role": "user", "content": "Run step"}])
    assert response.content == '{"status": "ok"}'
```

### Template 3: Real Integration Test

```python
import os
import pytest
from product_value_system.adapters.openai_adapter import OpenAIAdapter
from tests.integration_real.conftest import RealIntegrationCostTracker, real_tests_enabled


@pytest.mark.integration_real
@pytest.mark.skipif(
    not real_tests_enabled() or not os.getenv("OPENAI_API_KEY"),
    reason="Requires RUN_REAL_INTEGRATION_TESTS=1 and OPENAI_API_KEY",
)
class TestMyRealIntegration:
    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_real_api_call(self, cost_tracker: RealIntegrationCostTracker) -> None:
        adapter = OpenAIAdapter(model="gpt-3.5-turbo")
        try:
            response = await adapter.invoke(
                messages=[{"role": "user", "content": "Reply with OK"}],
                max_tokens=10,
                temperature=0.0,
            )
            if response.usage is not None:
                cost_tracker.track(adapter.calculate_cost(response.usage))
            assert "ok" in response.content.lower()
        finally:
            await adapter.close()
```

---

## Troubleshooting

### 1. `ModuleNotFoundError: No module named 'product_value_system'`
Install the package in editable mode inside your active virtual environment:
```bash
pip install -e ".[dev]"
```

### 2. `pytest: error: unrecognized arguments` or missing `timeout` marker
Ensure development dependencies (including `pytest-timeout` and `pytest-asyncio`) are installed:
```bash
pip install -e ".[dev]"
```

### 3. `401 Unauthorized` in real integration tests
Verify that `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is exported without trailing whitespace or expired credentials:
```bash
curl -s https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY" | head -c 200
```

### 4. `DockerException: Error while fetching server API version`
Start Docker Desktop (macOS/Windows) or `sudo systemctl start docker` (Linux), then build the sandbox image:
```bash
docker ps
make docker-build-sandbox
```
*(Live Docker tests automatically skip when the Docker daemon is offline.)*

### 5. Tests hang indefinitely
- Ensure every live test carries `@pytest.mark.timeout(60)`
- Verify async coroutines are properly `await`ed
- Confirm mock SSE responses include terminal markers (`data: [DONE]` for OpenAI, `event: message_stop` for Anthropic)

### 6. `BudgetExhausted` or `EmergencyStopTriggered`
Check if `EMERGENCY_STOP=1` is set in your shell or if `~/.pvs/budget.db` has reached the daily/monthly cap:
```bash
unset EMERGENCY_STOP
rm -f ~/.pvs/budget.db
```

### 7. `SecurityError: Input blocked` during tests
`Harness.invoke_model()` validates all `user` messages against `tests/security/injection_patterns.yaml`. Ensure test inputs do not contain adversarial prompt-injection triggers unless specifically testing security blocking.

### 8. `Unknown pytest.mark.<name>` (`--strict-markers`)
Register any new marker in [pytest.ini](../pytest.ini) under `markers =` before using it.

---

## CI/CD Integration

### Automated Workflows

| Workflow | Trigger | What Runs | Cost |
|----------|---------|-----------|------|
| [.github/workflows/ci.yml](../.github/workflows/ci.yml) | Every push & PR | `ruff`, `mypy`, unit + mocked integration + eval + security tests, Docker build | Free |
| [.github/workflows/policy-validation.yml](../.github/workflows/policy-validation.yml) | PRs touching `policies/` or `policy/` | Installs Cedar CLI, runs `validate-policy-sync.py --require-cedar-cli`, `tests/policy/` | Free |
| [.github/workflows/secret-scan.yml](../.github/workflows/secret-scan.yml) | Every push & PR | Secret detection across repository & commits | Free |
| [.github/workflows/integration-real.yml](../.github/workflows/integration-real.yml) | Nightly (03:00 UTC) & manual dispatch | Builds `pvs/agent-sandbox:latest`, runs `tests/integration_real/` with cost caps & container cleanup | ~$0.33 |

---

## Cost Management

Real integration tests enforce strict automated cost limits configured in [tests/integration_real/cost_limits.yaml](integration_real/cost_limits.yaml) and enforced in [tests/integration_real/conftest.py](integration_real/conftest.py):

- **Per-test hard cap**: `$0.05` (enforced by autouse `cost_per_test` fixture)
- **Per-run hard cap**: `$0.50` (enforced immediately by `RealIntegrationCostTracker.track()`)
- **Runtime budget guards**: Enforced inside `Budget` and `Harness.invoke_model()` with `--max-cost`, `--daily-budget`, `--monthly-budget`, and `EMERGENCY_STOP=1`. See [docs/COST_MANAGEMENT.md](../docs/COST_MANAGEMENT.md).

---

## Best Practices

### General
- Write unit tests alongside or before implementation (TDD)
- Keep assertions focused and failure messages informative
- Use descriptive names (`test_discovery_agent_rejects_invalid_problem`)
- Clean up resources in `finally` blocks or `yield` fixtures

### Async & Streaming
- Close `httpx.AsyncClient` resources via `await adapter.close()`
- Use `tests/fixtures/mock_sse_server.py` (`MockSSEResponse`, `create_openai_sse_chunks`, `create_anthropic_sse_chunks`) for deterministic SSE streaming tests

### Coverage & Quality Gate
- **Target coverage**: `>= 80%` across `src/product_value_system` (verify with `pytest --cov=product_value_system`)
- Always run `make lint` (`ruff check src/ tests/ scripts/` and `mypy src/`) before opening a pull request

---

## Resources

- **Main README**: [README.md](../README.md)
- **Architecture**: [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)
- **Security Testing Guide**: [docs/SECURITY_TESTING.md](../docs/SECURITY_TESTING.md)
- **Policy Sync Guidelines**: [docs/POLICY_SYNC.md](../docs/POLICY_SYNC.md)
- **Cost Management Guide**: [docs/COST_MANAGEMENT.md](../docs/COST_MANAGEMENT.md)
- **Contributing Guide**: [CONTRIBUTING.md](../CONTRIBUTING.md)
- **External Docs**: [pytest](https://docs.pytest.org/) · [pytest-asyncio](https://pytest-asyncio.readthedocs.io/) · [Docker SDK for Python](https://docker-py.readthedocs.io/)
