# ADR-0006: Brownfield Modularity Detection and Non-Punitive SPACE/DORA Governance

**Status**: Accepted  
**Date**: 2026-11-22  
**Authors**: @architecture-team  
**Related Requirements**: REQ-043, GAP-GIT-HISTORY, GAP-STRANGLER-FIG, GAP-DEVX-METRICS  
**Related Code**: `src/product_value_system/brownfield/boundary_analyzer.py`, `src/product_value_system/brownfield/acl_generator.py`, `src/product_value_system/metrics/devx_tracker.py`, `src/product_value_system/metrics/space_calculator.py`  

## Context

Most enterprise software investment targets brownfield monoliths rather than greenfield prototypes. Big-bang rewrites fail ~70% of the time, while naive connected-component analysis collapses bridged monoliths into a single cluster. Simultaneously, AI-native transformation requires measuring developer productivity holistically without triggering Goodhart's Law or individual surveillance backlash (Forsgren et al., *The SPACE of Developer Productivity*, ACM Queue 2021).

## Decision

1. **Graph-Theoretic Brownfield Modernization**:
   - Combine read-only Git temporal forensics (`GitAnalyzer`: hotspots, temporal coupling, age, bus-factor ownership) with **Clauset-Newman-Moore greedy modularity community detection** (`BoundaryAnalyzer`) scored by **Newman-Girvan structural modularity $Q$**.
   - Generate bidirectional declarative Anti-Corruption Layers (`ACLGenerator`) supporting **GitHub Scientist-style shadow-mode parity validation** and deterministic SHA-256 progressive Strangler Fig traffic routing (`1% -> 5% -> 25% -> 50% -> 100%` with instant `0%` rollback).
2. **Privacy-First, Non-Punitive DevX Governance**:
   - Instrument the orchestrator across all five **SPACE** dimensions + **DORA** delivery metrics (`DevXTracker`, `SPACECalculator`, `SatisfactionSurvey`), enforcing `aggregate_only: true` and `anonymize: true` in both YAML contracts (`config/metrics.yaml`) and HTTP `/devx` responses.

## Alternatives Considered

### 1. Connected Components for Monolith Clustering
**Pros**: Simple BFS traversal.  
**Cons**: A single shared utility or cross-domain bridge merges the entire monolith into one component.  
**Verdict**: Replaced with greedy modularity community detection achieving empirical $Q = 0.7336$ on bridged 60-file monoliths.

### 2. Individual Developer Leaderboards for AI Productivity
**Pros**: Granular attribution.  
**Cons**: Violates SPACE framework guidance, induces gaming (Goodhart's Law), and erodes engineering psychological safety.  
**Verdict**: Explicitly rejected; PII is hashed/dropped and endpoints expose team-level aggregates only.

## Consequences

### Positive
- Data-driven prioritization focuses AI modernization on high-churn, high-complexity hotspots instead of stable code.
- Shadow-mode ACL parity checks surface behavioral regressions before cutting over real user traffic.
- Unified `/metrics` and `/devx` endpoints feed Grafana dashboards (`dashboards/devx_dashboard.json`) with zero individual surveillance.

### Negative
- Shadow-mode execution temporarily doubles read load on migrated paths while parity validation is enabled (configurable per-module).

## Validation

- [x] Brownfield modularity & shadow-mode ACL suites pass (`tests/unit/test_strangler_planner.py`, `tests/integration/test_strangler_modernization.py`, `tests/unit/test_git_analyzer.py`)
- [x] Non-punitive SPACE/DORA & Prometheus integration suites pass (`tests/unit/test_devx_tracker.py`, `tests/integration/test_devx_metrics.py`, `tests/integration/test_monitoring_endpoints.py`)

## References
- [Git History Analysis Guide](../git-history-analysis.md)
- [Strangler Fig Modernization Guide](../strangler-fig-modernization.md)
- [DevX Metrics Guide](../devx-metrics.md)

## Metadata

```yaml
id: ADR-0006
status: accepted
date: '2026-11-22'
authors:
- architecture-team
supersedes: []
related_requirements:
- REQ-043
- GAP-GIT-HISTORY
- GAP-STRANGLER-FIG
- GAP-DEVX-METRICS
related_code:
- src/product_value_system/brownfield/boundary_analyzer.py
- src/product_value_system/brownfield/acl_generator.py
- src/product_value_system/metrics/devx_tracker.py
- src/product_value_system/metrics/space_calculator.py
tags:
- brownfield
- strangler-fig
- modularity
- devx
- space
- dora
```
