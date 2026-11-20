# Multi-Agent Review Guide

The AI-Native Product Value System runs **four independent specialized review agents** in parallel using the **Elephant-Goldfish pattern**:

| Reviewer Agent | Focus Area | Checklist Highlights |
|----------------|------------|----------------------|
| `SecurityReviewAgent` | Security & Compliance | OWASP LLM Top 10 (LLM01, LLM02, LLM04, LLM06, LLM07, LLM08), auth/authz, encryption, secrets, audit logging |
| `PerformanceReviewAgent` | Latency & Efficiency | Algorithmic complexity (`O(n^2)`), memory streaming/pagination, N+1 I/O, caching, connection pooling, latency budgets |
| `MaintainabilityReviewAgent` | Clarity & Testability | Unambiguous requirements, acceptance criteria, unit/integration/acceptance coverage, modularity, technical debt |
| `ArchitectureReviewAgent` | Structural Soundness | Architectural decisions & trade-offs, coupling, SPOFs, idempotency, scalability, resilience |

---

## Finding Severity Taxonomy

| Severity | Meaning | Gate Behavior |
|----------|---------|---------------|
| `CRITICAL` | Exploitable vulnerability, guaranteed outage, or fundamental architectural flaw | **Blocks release** (`gate_decision = FAIL`, `verdict = REJECTED`) |
| `HIGH` | Significant risk requiring remediation before launch | Allowed up to `max_high_findings` (default `5`); >5 triggers `FAIL` |
| `MEDIUM` | Important improvement; track in backlog | Informational (`PASS`) |
| `LOW` | Minor polish or defense-in-depth suggestion | Informational (`PASS`) |

---

## Running Multi-Agent Reviews

```bash
# Review a specification file with all 4 independent perspectives
pvs review artifacts/runs/<problem-id>/specification.yaml --output consolidated_review.yaml

# Offline deterministic mock smoke check ($0 cost)
pvs review artifacts/runs/<problem-id>/specification.yaml --use-mock

# Opt in to parallel 4-agent review during a value stream run
pvs run examples/greenfield/problem_payment_flow.yaml --multi-review
```

---

## Interpreting Consolidated Output

Each `ReviewResult` (saved as `consolidated_review.yaml` during orchestrated runs) includes:

- **`verdict`**: `APPROVED`, `APPROVED_WITH_CONCERNS`, or `REJECTED`
- **`gate_decision`**: `PASS` or `FAIL` with human-readable `gate_reason`
- **`summary`**: Counts by severity (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`)
- **`findings`**: Prioritized list sorted `CRITICAL → LOW`, then domain precedence (`security → architecture → performance → scalability → observability → maintainability`)
- **`conflicts`**: Any contradictory recommendations across reviewers with explicit resolution and rationale

See [Multi-Agent Review Architecture](architecture/multi-agent-review.md), [Conflict Resolution Patterns](developer/conflict-resolution.md), and [Adding Custom Reviewers](developer/custom-reviewers.md).
