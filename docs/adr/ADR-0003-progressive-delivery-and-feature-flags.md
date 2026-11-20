# ADR-0003: Consistent SHA-256 Feature Flags and Progressive Delivery

**Status**: Accepted  
**Date**: 2026-11-20  
**Authors**: @platform-team  
**Related Requirements**: GAP-PROGRESSIVE-DELIVERY, GAP-PRODUCTION-MONITORING  
**Related Code**: `src/product_value_system/feature_flags/service.py`, `src/product_value_system/feature_flags/rollout.py`  

## Context

All-or-nothing releases of new prompts, agents, and review pipelines expose 100% of users to regressions simultaneously and require full code redeploys to revert. We need progressive delivery (dark launch -> canary -> staged -> full) coupled directly to EVO-014 SLO quality gates with sub-millisecond evaluation latency and zero external SaaS runtime dependencies.

## Decision

We will implement a **Git-backed, schema-validated YAML feature flag system** (`flags.yaml`) using consistent SHA-256 bucketing (`SHA256(flag_name:user_id) % 10000`) and a 6-stage `RolloutController` wired to `PVSMetrics` for automatic SLO-breach rollback.

## Alternatives Considered

### 1. External SaaS Feature Flag Service (LaunchDarkly)

**Pros**:
- Rich hosted dashboard and enterprise RBAC out of the box

**Cons**:
- External network dependency, vendor billing, and inability to run offline in local/CI environments

**Verdict**: Rejected as core dependency; remains optional future enterprise adapter.

### 2. Environment-Variable Boolean Toggles

**Pros**:
- Zero code complexity

**Cons**:
- No per-user percentage bucketing, no targeting rules, and requires process restart to roll back

**Verdict**: Rejected because it cannot support canary or staged rollouts.

## Consequences

### Positive

- Sub-millisecond (`<1ms`) local evaluation with stable user assignment
- Instant rollback (`flag.enabled = False`) triggered automatically on error rate >1%, quality <95%, or CRITICAL alerts
- Full audit log and FastAPI Admin UI (`/admin/flags`) with optional API-key authentication

### Negative

- Single-node file persistence (`flags.yaml`) requires Git/shared volume sync in multi-replica clusters

### Neutral

- Teams should retire completed `release` flags after 14 days at `full` rollout while keeping `ops` kill switches permanently

## Implementation Plan

1. Define `schemas/feature_flags.schema.json` and `FeatureFlagService`.
2. Build 6-stage `RolloutController` bridged to EVO-014 `PVSMetrics`.
3. Add FastAPI Admin UI, `pvs flags` CLI, and `ValueStreamOrchestrator` gating.

## Validation

- [ ] Flag evaluation averages <1ms with uniform SHA-256 bucketing
- [ ] Error rate >1% or quality <95% automatically disables flag and alerts team
- [ ] Schema validation rejects malformed flag configurations on load and save

## References

- [Progressive Delivery Guide](../progressive-delivery.md)
- [Flag Management Guide](../flag-management.md)
- [SLO & Error Budget Guide](../slo-guide.md)

## Metadata

```yaml
id: ADR-0003
status: accepted
date: '2026-11-20'
authors:
- platform-team
supersedes: []
related_requirements:
- GAP-PROGRESSIVE-DELIVERY
- GAP-PRODUCTION-MONITORING
related_code:
- src/product_value_system/feature_flags/service.py
- src/product_value_system/feature_flags/rollout.py
tags:
- progressive-delivery
- feature-flags
- sre
- rollback
```
