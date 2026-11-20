# Automated Drift Detection Guide (EVO-017)

The Drift Detection engine (`product_value_system.drift`) continuously compares Python source code, Markdown/OpenAPI specifications, and OpenTelemetry runtime traces to catch silent divergence before it reaches customers.

---

## Architecture

1. **`CodeScanner`** (`src/product_value_system/drift/code_scanner.py`): AST-parses Python functions (`def` and `async def`), class methods, type annotations, defaults, `*args`/`**kwargs`, docstrings, and FastAPI/Starlette HTTP routes with mtime AST caching (`<500ms` for 50-function modules).
2. **`SpecScanner`** (`src/product_value_system/drift/spec_scanner.py`): Extracts function contracts from Markdown fenced Python blocks (with exact line numbers), endpoint schemas from OpenAPI 3.x YAML/JSON, and performance/error SLA clauses.
3. **`TraceScanner`** (`src/product_value_system/drift/trace_scanner.py`): Aggregates OpenTelemetry span JSON/JSONL exports (`mean`, `p50`, `p95`, `error_rate`) and verifies them against specified SLAs.
4. **`DriftComparator`** (`src/product_value_system/drift/comparator.py`): Classifies structural, endpoint, stale-doc, and optional LLM semantic docstring drift into `CRITICAL`, `MAJOR`, `MODERATE`, and `MINOR` findings with actionable fix locations.
5. **`DriftReporter`** (`src/product_value_system/drift/reporter.py`): Generates JSON and Markdown reports, weekly digests, brownfield baseline filters, and pre-commit blocking decisions.

---

## Severity Taxonomy & Commit Gating

| Severity | Meaning | Default Commit Gate |
| :--- | :--- | :--- |
| `CRITICAL` | Breaking change: parameter/function removed from code, or error-rate SLA breached | Always blocks commit |
| `MAJOR` | Required parameter added, parameter/return type mismatch, endpoint removed, or latency SLA exceeded | Blocks when `>3` findings (or `>5%` drift ratio) |
| `MODERATE` | Optional parameter with default added, or semantic docstring mismatch | Warning |
| `MINOR` | Default value tweak or stale documentation lag | Informational |

---

## Complete Examples

### Example 1: Added Parameter Drift (`initiate_transfer`)

**Implementation (`src/payment/transfer.py`)**:

```python
async def initiate_transfer(
    amount: float,
    destination: str,
    require_biometric: bool = False,
) -> dict:
    """Initiate payment transfer with optional biometric verification."""
    return {"status": "pending"}
```

**Specification (`docs/api/transfer.md`)**:

````markdown
```python
def initiate_transfer(amount: float, destination: str) -> dict:
    """Initiate payment transfer."""
```
````

**CLI Output**:

```bash
pvs drift check --code src/payment/ --spec docs/api/
# [MODERATE] Function `initiate_transfer()` added parameter 'require_biometric' not in specification
#   Code: src/payment/transfer.py::initiate_transfer
#   Spec: docs/api/transfer.md:L2
#   Fix:  Update spec at `docs/api/transfer.md` line 2: add `require_biometric: bool = False`
```

---

### Example 2: Behavioral SLA Drift from OpenTelemetry Traces

**Specification (`docs/performance.md`)**:

```markdown
## initiate_transfer
`initiate_transfer` should complete in 2-5 seconds under normal load.
```

**OpenTelemetry Spans (`traces/payment.json`)**:

```json
{
  "spans": [
    {"operation": "initiate_transfer", "duration_ms": 8234},
    {"operation": "initiate_transfer", "duration_ms": 7891},
    {"operation": "initiate_transfer", "duration_ms": 8567}
  ]
}
```

**CLI Command**:

```bash
pvs drift check --code src/ --spec docs/performance.md --include-behavior --traces traces/payment.json
```

Detects `[MAJOR] Actual performance for 'initiate_transfer' exceeds specified SLA (8.23s avg vs 2.0-5.0s)` with `p50` and `p95` details.

---

### Example 3: Semantic Docstring Drift

Pass an `llm_callable` to `DriftComparator` / `detect_drift` to flag cases where signatures still match but documented behavior changed (for example, switching from a flat 2% fee in the spec to progressive tiers in the docstring):

```python
from product_value_system.drift import detect_drift

findings = detect_drift(
    code_paths=["src/payment/"],
    spec_paths=["docs/api/"],
    llm_callable=my_llm_compare_fn,
)
```

---

## Brownfield Adoption & Pre-Commit Hook

Existing brownfield codebases can adopt drift detection incrementally without getting overwhelmed:

```bash
# 1. Capture initial brownfield baseline snapshot
pvs drift check --code src/ --spec docs/ --save-baseline initial_drift.json

# 2. Enforce zero NEW critical/major drift relative to baseline
pvs drift check --code src/ --spec docs/ --baseline initial_drift.json --severity critical,major

# 3. Generate weekly Markdown drift digest
pvs drift report --code src/ --spec docs/ --since "7 days ago" --output weekly_drift.md

# 4. Install Git pre-commit hook
pvs drift install-hook
```

---

## Related Documentation

- [SLO & Error Budget Guide](slo-guide.md)
- [Multi-Agent Independent Review Guide](review-guide.md)
- [Progressive Delivery Guide](progressive-delivery.md)
