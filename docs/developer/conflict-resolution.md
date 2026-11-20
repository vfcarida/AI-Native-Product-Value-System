# Conflict Resolution Patterns

When specialized reviewers inspect the same requirement (`requirement_id`) from independent perspectives, recommendations can conflict. `ReviewSynthesizer` ([`src/product_value_system/review/synthesizer.py`](../../src/product_value_system/review/synthesizer.py)) detects and resolves these conflicts deterministically.

---

## Supported Tradeoff Patterns

| Conflict Type | Typical Tension | Example |
|---------------|-----------------|---------|
| `security_vs_performance` | Encryption / validation / auditing vs skipping checks or caching plaintext | Security: *"Encrypt payload at rest"* vs Performance: *"Skip encryption for lower latency"* |
| `performance_vs_maintainability` | Inlining / denormalization / custom caches vs modularity / simplicity | Performance: *"Denormalize hot table & inline parser"* vs Maintainability: *"Normalize schema & simplify module"* |
| `security_vs_maintainability` | Strict isolation / RBAC / separation vs consolidating layers | Security: *"Isolate tenant roles with strict RBAC"* vs Maintainability: *"Merge into single-role helper"* |
| `architecture_vs_performance` | Decoupling / async event-driven boundaries vs direct synchronous calls | Architecture: *"Decouple via async event bus"* vs Performance: *"Inline synchronous call"* |

---

## Resolution Precedence Rules

1. **Severity Rank First**: `CRITICAL (4) > HIGH (3) > MEDIUM (2) > LOW (1)`. A higher-severity finding always wins regardless of category.
2. **Domain Precedence on Equal Severity**:
   `security > architecture > performance > scalability > observability > maintainability`
3. **Optional LLM Rationale Refinement**: Pass `ReviewSynthesizer(llm_judge=model_adapter)` to generate contextual 1-sentence tradeoff explanations while preserving the deterministic winning choice.

---

## Extending Conflict Patterns

Add new `(FindingCategory, FindingCategory)` keyword pairs to `CONFLICT_KEYWORD_PAIRS` in `src/product_value_system/review/synthesizer.py` or subclass `ReviewSynthesizer._are_conflicting()`.
