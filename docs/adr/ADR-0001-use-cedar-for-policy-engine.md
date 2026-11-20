# ADR-0001: Use Cedar for Policy Engine

**Status**: Accepted  
**Date**: 2026-11-20  
**Authors**: @architecture-team  
**Related Requirements**: REQ-063, REQ-SEC-001  
**Related Code**: `src/product_value_system/policy/engine.py`, `policies/agent_authorization.cedar`  

## Context

Agents execute tool calls, filesystem writes, and external actions inside automated value-stream runs. We need a declarative authorization engine that satisfies:
- Human-readable, Git-versioned policy definitions separate from application code
- Fine-grained principal/action/resource evaluation with sub-10ms latency
- Formal schema validation (`policies/schema.cedarschema`) and deterministic audit logging
- Deterministic local fallback synchronized in CI when the `cedar` binary is unavailable

Hard-coding `if` authorization checks inside agent classes couples security controls to code deploys and increases privilege escalation risk.

## Decision

We will use **AWS Cedar** (`policies/agent_authorization.cedar`) as our primary policy authorization engine, paired with a CI-verified Python fallback evaluator (`scripts/validate-policy-sync.py`).

## Alternatives Considered

### 1. Open Policy Agent (OPA / Rego)

**Pros**:
- Mature CNCF ecosystem with Kubernetes and Envoy integrations
- Expressive general-purpose policy language (Rego)

**Cons**:
- Higher evaluation latency for complex policy bundles
- Steeper learning curve for product engineers writing authorization rules
- Lacks first-class partial evaluation ergonomics for schema-typed entity graphs

**Verdict**: Rejected due to schema ergonomics and higher operational overhead.

### 2. Custom In-Code RBAC

**Pros**:
- Zero external binary or syntax dependencies
- Simple initial implementation

**Cons**:
- Security-critical authorization logic scattered across Python modules
- No formal schema verification or declarative policy diffing

**Verdict**: Rejected due to security maintenance risk and lack of auditability.

## Consequences

### Positive

- Policy changes are declarative, version-controlled, and validated against `schema.cedarschema`
- Automated CI sync validation (`tests/policy/test_policy_fallback_sync.py`) guarantees Cedar and Python fallback parity
- Sub-10ms authorization checks with structured decision telemetry

### Negative

- Team must learn Cedar syntax and maintain policy/fallback synchronization tests
- Smaller third-party integration ecosystem than OPA

### Neutral

- Policy updates require running `scripts/validate-policy-sync.py` in CI

## Implementation Plan

1. Define entity schema in `policies/schema.cedarschema` and rules in `policies/agent_authorization.cedar`.
2. Implement `PolicyEngine` with Cedar CLI evaluation and deterministic Python fallback.
3. Enforce parity across all principal/action/resource combinations in CI.

## Validation

- [ ] `scripts/validate-policy-sync.py` passes with 100% parity
- [ ] All policy violations emit structured audit logs and OpenTelemetry metrics
- [ ] Authorization checks complete in <10ms p95

## References

- [Cedar Policy Language Documentation](https://www.cedarpolicy.com/)
- [Policy Synchronization Guide](../POLICY_SYNC.md)

## Metadata

```yaml
id: ADR-0001
status: accepted
date: '2026-11-20'
authors:
- architecture-team
supersedes: []
related_requirements:
- REQ-063
- REQ-SEC-001
related_code:
- src/product_value_system/policy/engine.py
- policies/agent_authorization.cedar
tags:
- security
- authorization
- policy
- cedar
```
