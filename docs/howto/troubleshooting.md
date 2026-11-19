# Troubleshooting Guide

Common problems and solutions.

---

## Installation Issues

### `ModuleNotFoundError: No module named 'product_value_system'`

**Cause**: Package not installed in the active virtual environment.

**Solution**:

```bash
pip install -e ".[dev]"
```

---

### `Docker daemon not running`

**Cause**: Docker Desktop or Docker Engine is not started.

**Solution**:

```bash
# macOS: Start Docker Desktop
open -a Docker

# Linux: Start Docker service
sudo systemctl start docker

# Windows: Start Docker Desktop from Start Menu
```

*(Note: Unit tests and demo mode use `MockSandboxProvider` / `FakeDockerClient` when Docker is offline.)*

---

## Runtime Errors

### `ValidationError: 'statement' is a required property`

**Cause**: Invalid `problem.yaml` (missing required fields).

**Solution**: Check problem against schema:

```bash
pvs validate problem.yaml --schema problem
```

Fix missing fields in `problem.yaml`. See [Problem Schema Reference](../reference/schemas/problem.md).

---

### `PolicyDeniedError` or `Decision.DENY`

**Cause**: Agent attempted an action blocked by Cedar policy.

**Solution**:

1. Review policy decisions in the SQLite database:

```bash
sqlite3 pvs.db "SELECT agent_name, action, resource, decision, reason FROM policy_decisions WHERE decision='deny' ORDER BY timestamp DESC LIMIT 10"
```

2. If the action should be allowed, update `policies/agent_authorization.cedar`. See [Define Cedar Policies](define-policies.md).

---

### `BudgetExceededError: Cost budget exceeded`

**Cause**: Agent exceeded its USD cost budget or maximum iteration limit.

**Solution**:

1. Increase budget in the agent configuration YAML:

```yaml
# config/agents/<agent_name>.yaml
spec:
  autonomy:
    maxIterations: 15
    maxCostUsd: 10.0
```

2. Or optimize agent prompts to reduce iterations and token usage.

---

### `SandboxError: Timeout`

**Cause**: Code execution in the sandbox exceeded `timeout_seconds` (default 300s).

**Solution**: Increase timeout in `SandboxConfig`:

```python
from product_value_system.runtime.sandbox import SandboxConfig

sandbox_config = SandboxConfig(
    timeout_seconds=600  # 10 minutes
)
```

---

## Model API Errors

### `ModelError: Invalid API key`

**Cause**: `OPENAI_API_KEY` not set or invalid.

**Solution**:

```bash
export OPENAI_API_KEY="sk-..."
pvs run problem.yaml
```

---

### `RateLimitError: Rate limit exceeded`

**Cause**: Too many requests to the LLM provider API.

**Solution**: Wait and retry, or unset `OPENAI_API_KEY` to run with the deterministic `MockModelAdapter` for local testing without external API calls:

```bash
pvs run problem.yaml
```

---

## Sandbox Issues

### `SandboxError: Failed to create sandbox`

**Cause**: `pvs/agent-sandbox:latest` Docker image is not built.

**Solution**: Build the agent sandbox image:

```bash
docker build -t pvs/agent-sandbox:latest docker/agent-sandbox/
```

---

### `PathTraversalError: Path escapes workspace`

**Cause**: File operation attempted to access a path outside the workspace root or matching a forbidden pattern.

**Solution**: Ensure all paths are relative to the workspace root and do not contain `..`:

```python
from pathlib import Path
from product_value_system.security.path_validation import validate_path

safe_path = validate_path("artifacts/output.json", workspace_root=Path.cwd())
```

---

## Policy Issues

### `FileNotFoundError: Policies not found`

**Cause**: `PolicyEngine` initialized from a working directory that does not contain `policies/agent_authorization.cedar`.

**Solution**: Pass the explicit `policies_dir` path or run from the repository root:

```bash
make validate-policies
```

---

## Evaluation Issues

### Judge fails or returns `score = 0.0`

**Cause**: Artifact is missing expected keys (`content`, `specification`, `test_coverage`, etc.) or AI judge returned non-JSON output.

**Solution**: Verify artifact structure matches the judge's expected input format. See [Write Evaluations](write-evaluations.md).

---

## Getting Help

If a problem persists:

1. **Enable debug logging**: `export PVS_LOG_LEVEL=DEBUG`
2. **Inspect run details**: `pvs show <run-id>`
3. **Run test suite**: `pytest -v`
4. **Check reference docs**: [API Reference](../reference/README.md)

---

**Last Updated**: 2026-11-18
