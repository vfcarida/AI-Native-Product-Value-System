# AI-Native Product Value System: Testing Guide

## Test Tiers

### 1. Unit Tests (Fast, Mocked)
**Location**: `tests/unit/`  
**Run**: `pytest tests/unit/`  
**Duration**: <10 seconds  
**Cost**: \$0 (all mocked)  
**Purpose**: Validate individual components in isolation.

**When to write**: For every new function, class, or adapter method.

### 2. Integration Tests (Fast, Mocked)
**Location**: `tests/integration/`  
**Run**: `pytest tests/integration/`  
**Duration**: <30 seconds  
**Cost**: \$0 (uses `MockModelAdapter` and `MockSandboxProvider`)  
**Purpose**: Validate agent workflows, CLI commands, and orchestration paths on every commit.

**When to write**: For agent workflows, CLI subcommands, and orchestration state transitions.

### 3. Real Integration Tests (Live Dependencies)
**Location**: `tests/integration_real/`  
**Run**: `RUN_REAL_INTEGRATION_TESTS=1 pytest tests/integration_real/ -v -s`  
**Duration**: 10–15 minutes  
**Cost**: ~\$0.33 per full run (hard cap: \$0.50/run, \$0.05/test)  
**Purpose**: Validate actual integration with live OpenAI/Anthropic APIs, real Docker Engine sandboxes, and Cedar CLI policies.

**Requirements**:
- `RUN_REAL_INTEGRATION_TESTS=1`
- `OPENAI_API_KEY` environment variable (for OpenAI & E2E tests)
- `ANTHROPIC_API_KEY` environment variable (for Anthropic tests)
- Active Docker daemon with `pvs/agent-sandbox:latest` built (`make docker-build-sandbox`)
- Optional: `cedar` CLI installed on `PATH` (Cedar CLI test skips gracefully if absent)

### 4. Security & Evaluation Tests
**Location**: `tests/security/` and `tests/eval/`  
**Run**: `pytest tests/security/ tests/eval/ -v`  
**Duration**: <15 seconds  
**Cost**: \$0  
**Purpose**: Validate prompt/command/path injection defenses, secret detection, Git hooks, deterministic judges, AI judges, and evaluation pipelines.

---

## Running Tests

### Quick Development Feedback
```bash
make test-unit
```

### Full Standard CI Suite (Fast & Free)
```bash
make test
```

### Real Integration Suite (Nightly / On-Demand)
```bash
export RUN_REAL_INTEGRATION_TESTS=1
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."

make docker-build-sandbox
make test-integration-real
```

### Full Quality Gate
```bash
make test-all
```

---

## Cost Management

Configured in `tests/integration_real/cost_limits.yaml` and enforced by `tests/integration_real/conftest.py`:

- **Per-Test Budget**: \$0.05 maximum
- **Per-Run Budget**: \$0.50 maximum
- **Fail-Fast**: `RealIntegrationCostTracker.track()` aborts immediately if the total suite spend exceeds \$0.50, and the autouse `cost_per_test` fixture fails any single test exceeding \$0.05.

---

## Writing Real Integration Tests

### Template

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
class TestMyFeatureReal:
    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_my_feature(self, cost_tracker: RealIntegrationCostTracker) -> None:
        adapter = OpenAIAdapter(model="gpt-3.5-turbo")
        try:
            response = await adapter.invoke(
                [{"role": "user", "content": "Say OK"}],
                max_tokens=10,
            )
            if response.usage is not None:
                cost_tracker.track(adapter.calculate_cost(response.usage))
            assert response.content
        finally:
            await adapter.close()
```

### Best Practices
1. Always use the cheapest model (`gpt-3.5-turbo` or `claude-3-haiku-20240307`).
2. Keep `max_tokens` small (`10`–`100`) where possible.
3. Always add `@pytest.mark.timeout(...)` to guard against hung network or container calls.
4. Always clean up resources (`await adapter.close()`, `await sandbox_provider.destroy(sandbox)` in `finally` blocks).
5. Always report spend via `cost_tracker.track(...)`.
