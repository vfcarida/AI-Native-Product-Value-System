# Architecture Decision Records (ADR) Guide (EVO-018)

Architecture Decision Records preserve the **context**, **alternatives considered**, and **consequences** behind significant technical choices so teams stop re-litigating settled architecture decisions.

---

## Quick Start CLI

```bash
# Create a new auto-numbered ADR (proposed by default)
pvs adr new "Use Cedar for Policy Engine" --author alice --req REQ-SEC-001 --tag security

# List all ADRs (table or JSON)
pvs adr list
pvs adr list --status accepted --format json

# Inspect a specific ADR
pvs adr show ADR-0001

# Full-text search across titles, context, decisions, alternatives, tags, and code links
pvs adr search "policy engine"

# Approve an ADR after human review
pvs adr accept ADR-0004 --reviewer bob

# Supersede an older ADR with a newer one
pvs adr supersede ADR-0001 ADR-0004

# Render Mermaid supersession graph
pvs adr graph

# Generate consolidated Markdown decision log for onboarding
pvs adr log --output docs/adr/DECISION_LOG.md

# Trace inline `# See ADR-XXXX` comments in source code
pvs adr scan-code --code src/
```

---

## Decision Agent Integration

Whenever an agent selects between architectural or technology options (optionally backed by EVO-016 A/B experiment results), use `DecisionAgentADRIntegration` to generate a structured **`proposed`** ADR draft automatically:

```python
from product_value_system.adr import ADRManager, DecisionAgentADRIntegration

manager = ADRManager(adr_dir="docs/adr")
integration = DecisionAgentADRIntegration(manager, llm_callable=my_llm)

adr_id = await integration.generate_adr_from_decision(
    decision_context={
        "title": "Select Vector Store Backend",
        "problem": "Need low-latency semantic retrieval for agent memory",
        "goal": "Sub-20ms p95 search with local dev parity",
    },
    alternatives=[
        {"name": "pgvector", "description": "PostgreSQL vector extension"},
        {"name": "Qdrant", "description": "Dedicated Rust vector database"},
    ],
    chosen_alternative="pgvector",
    experiment_results={
        "EXP-VEC-001": {"winner": "pgvector", "confidence": 0.96, "metrics": {"p95_ms": 11.4}}
    },
)

# Human architect review gate before marking as accepted
integration.approve_adr(adr_id, reviewer="alice")
```

All agent-generated ADRs remain `proposed` until explicitly accepted by a human reviewer.

---

## Linking Code to ADRs

Reference ADRs inline in source code comments so future maintainers can jump straight to the rationale:

```python
# See ADR-0001 for why AWS Cedar is used instead of custom RBAC
decision = policy_engine.authorize(principal, action, resource)
```

Run `pvs adr scan-code --code src/` to list every code line referencing an ADR.

---

## Example ADRs in This Repository

- [ADR Index](adr/README.md)
- [ADR-0001: Use Cedar for Policy Engine](adr/ADR-0001-use-cedar-for-policy-engine.md)
- [ADR-0002: Elephant-Goldfish Multi-Agent Independent Review](adr/ADR-0002-elephant-goldfish-multi-agent-review.md)
- [ADR-0003: Consistent SHA-256 Feature Flags and Progressive Delivery](adr/ADR-0003-progressive-delivery-and-feature-flags.md)
