# ADR-0002: Elephant-Goldfish Multi-Agent Independent Review

**Status**: Accepted  
**Date**: 2026-11-20  
**Authors**: @architecture-team  
**Related Requirements**: REQ-051, GAP-INDEPENDENT-REVIEW  
**Related Code**: `src/product_value_system/review/orchestrator.py`, `src/product_value_system/review/synthesizer.py`  

## Context

A single architecture reviewer inspecting specifications carries author confirmation bias and misses cross-cutting security, performance, and maintainability defects. Conversely, running sequential reviews increases wall-clock latency linearly and leaks context between reviewers.

## Decision

We will implement the **Elephant-Goldfish Multi-Agent Independent Review** pattern:
- The authoring agent retains full context (Elephant).
- Four specialized reviewers (**Security**, **Performance**, **Maintainability**, **Architecture**) clear memory before each run (Goldfish) and execute concurrently via `asyncio.gather`.
- A deterministic `ReviewSynthesizer` deduplicates findings, resolves trade-off conflicts (prioritizing `Security > Maintainability > Performance`), and enforces release gates (`0 CRITICAL`, `<= 5 HIGH`).

## Alternatives Considered

### 1. Single Multi-Checklist Reviewer Prompt

**Pros**:
- Lower single-call token cost
- Simpler orchestration code

**Cons**:
- Context dilution reduces recall on specialized OWASP security and latency bottlenecks
- No explicit conflict resolution between competing quality attributes

**Verdict**: Rejected due to lower defect detection recall.

### 2. Sequential Chained Reviews

**Pros**:
- Downstream reviewers see earlier findings

**Cons**:
- Anchoring bias causes later reviewers to echo earlier findings instead of independent analysis
- 4x slower end-to-end latency

**Verdict**: Rejected due to anchoring bias and latency penalty.

## Consequences

### Positive

- Zero confirmation or anchoring bias via `Memory.clear()` isolation per reviewer
- Parallel execution keeps wall-clock review time close to a single reviewer
- Explicit, deterministic conflict resolution and prioritized finding taxonomy

### Negative

- Higher aggregate token usage (4 concurrent reviewer passes vs 1)

### Neutral

- Opt-in via `pvs run --multi-review`, `pvs review <spec>`, or the `multi_agent_review` feature flag

## Implementation Plan

1. Add `Memory.clear()` and `BaseReviewAgent` enforcing fresh context per review.
2. Implement Security, Performance, Maintainability, and Architecture review specialists.
3. Build `ReviewSynthesizer` and `MultiAgentReviewOrchestrator` with CLI integration.

## Validation

- [ ] All 4 reviewers run with cleared memory and produce structured `ReviewFinding` lists
- [ ] Conflict resolution cleanly prioritizes Security over Performance trade-offs
- [ ] Consolidated review gate blocks specs with CRITICAL findings

## References

- [Multi-Agent Review Guide](../review-guide.md)
- [Multi-Agent Review Architecture](../architecture/multi-agent-review.md)

## Metadata

```yaml
id: ADR-0002
status: accepted
date: '2026-11-20'
authors:
- architecture-team
supersedes: []
related_requirements:
- REQ-051
- GAP-INDEPENDENT-REVIEW
related_code:
- src/product_value_system/review/orchestrator.py
- src/product_value_system/review/synthesizer.py
tags:
- review
- multi-agent
- quality
- architecture
```
