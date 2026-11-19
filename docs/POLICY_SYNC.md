# Policy Synchronization Guidelines

## Overview

Authorization policies exist in two synchronized locations:

1. **Authoritative Cedar policies**: `policies/agent_authorization.cedar` (and schema `policies/schema.cedarschema`)
2. **Python fallback evaluator**: `PolicyEngine._fallback_evaluate()` in `src/product_value_system/policy/engine.py`

When the Rust `cedar` CLI binary is installed, `PolicyEngine` delegates authorization to Cedar. When `cedar` is unavailable (e.g., restricted developer workstations), `PolicyEngine` falls back to `_fallback_evaluate()`. **Both paths MUST produce identical `ALLOW` / `DENY` decisions.**

---

## Adding or Modifying a Policy

### Step 1: Update the Authoritative Cedar Policy

Edit `policies/agent_authorization.cedar`:

```cedar
permit (
    principal in ProductValueSystem::AgentRole::"my_agent",
    action == ProductValueSystem::Action::"read",
    resource in ProductValueSystem::Tool::"my_tool"
) when {
    context.environment == "sandbox"
};
```

### Step 2: Update the Python Fallback Evaluator

Mirror the rule in `PolicyEngine._fallback_evaluate()` (`src/product_value_system/policy/engine.py`). Always keep `forbid` checks before `permit` checks (Cedar forbid-overrides-permit semantics) and retain deny-by-default at the end of the method.

### Step 3: Add Sync Validation Test Cases

Add positive and negative test cases to `PolicyValidator.generate_test_cases()` in `scripts/validate-policy-sync.py`.

### Step 4: Run Automated Sync Validation

```bash
# Run CLI validation script (uses Cedar CLI if installed, else independent .cedar parser)
python scripts/validate-policy-sync.py --verbose --output policy-validation-report.json

# Or via Makefile
make validate-policies

# Run pytest wrapper (includes intentional-drift detection test)
pytest tests/policy/test_policy_fallback_sync.py -v
```

For strict validation requiring the official Cedar CLI binary:

```bash
python scripts/validate-policy-sync.py --verbose --require-cedar-cli
```

---

## CI Enforcement

Pull requests modifying `policies/**`, `src/product_value_system/policy/**`, `scripts/validate-policy-sync.py`, or `tests/policy/**` trigger `.github/workflows/policy-validation.yml`, which:

1. Installs `cedar-policy-cli` via Cargo
2. Runs `scripts/validate-policy-sync.py --verbose --require-cedar-cli --output policy-validation-report.json`
3. Runs `pytest tests/policy/test_policy_fallback_sync.py -v`
4. Fails the build immediately if any policy mismatch is detected
