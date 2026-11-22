# Strangler Fig Modernization Guide (EVO-031)

The Strangler Fig pattern (Martin Fowler, 2004; Sam Newman, *Monolith to Microservices*) replaces high-pain legacy modules incrementally behind an Anti-Corruption Layer (ACL) and progressive traffic routing (`1% → 5% → 25% → 50% → 100%`), eliminating big-bang rewrite risk while keeping production continuously online and instantly reversible.

---

## How Strangler Fig Planning Works

```
Legacy Codebase + GitInsights (EVO-030)
       │
       ▼
  BoundaryAnalyzer   ── static package grouping / NetworkX import-graph clustering
       │                (filters < min_module_size_lines, splits > max_module_size_lines)
       ▼
  StranglerPlanner   ── weighted score: hotspot (0.4) + complexity (0.3)
       │                                + low coupling (0.2) + business value (0.1)
       ├─▶ 8-Step Replacement Plan (effort estimates, prerequisites, risk levels)
       ├─▶ ACLGenerator            (AST public API extraction → compilable Python ACL)
       ├─▶ RoutingStrategyPlanner  (feature_flag / reverse_proxy / api_gateway)
       └─▶ Risk Assessment         (coupling, complexity, module size, knowledge silos)
```

Core modules:
- [src/product_value_system/brownfield/boundary_analyzer.py](../src/product_value_system/brownfield/boundary_analyzer.py) — `BoundaryAnalyzer`, `Module`, `normalize_priority_weights()`
- [src/product_value_system/brownfield/strangler_planner.py](../src/product_value_system/brownfield/strangler_planner.py) — `StranglerPlanner`, `StranglerPlan`, `ReplacementStep`, `load_strangler_config()`
- [src/product_value_system/brownfield/acl_generator.py](../src/product_value_system/brownfield/acl_generator.py) — `ACLGenerator`, `APIFunctionSpec`
- [src/product_value_system/brownfield/routing_strategy.py](../src/product_value_system/brownfield/routing_strategy.py) — `RoutingStrategyPlanner`, `RoutingStrategyType`, `TrafficRoutingPlan`, `RolloutStage`, `should_route_to_modern()`
- [src/product_value_system/agents/brownfield_agent.py](../src/product_value_system/agents/brownfield_agent.py) — `BrownfieldAgent` end-to-end orchestration
- [config/brownfield.yaml](../config/brownfield.yaml) — YAML configuration

---

## Candidate Selection Criteria

| Factor | Default Weight | Why It Matters |
|---|---:|---|
| `hotspot_score` | `+0.4` | High-churn modules cause the highest ongoing maintenance pain |
| `complexity_score` | `+0.3` | High cyclomatic complexity drives defect density |
| `coupling_score` | `-0.2` | **Low** coupling makes extraction safe without cross-system rewrites |
| `business_value` | `+0.1` | Allows stakeholders to elevate customer-facing domains |

---

## Configuration (`config/brownfield.yaml`)

```yaml
brownfield:
  strangler_fig:
    enabled: true
    top_candidates: 3

    boundary_detection:
      method: static_analysis       # 'static_analysis', 'dependency_graph', or 'ml'
      min_module_size_lines: 500
      max_module_size_lines: 10000

    priority_factors:
      - hotspot_score: 0.4
      - complexity_score: 0.3
      - coupling_score: -0.2
      - business_value: 0.1

    routing:
      strategy: feature_flag        # 'feature_flag', 'reverse_proxy', or 'api_gateway'
      initial_percentage: 1
      stages: [1, 5, 25, 50, 100]
      monitoring_duration_per_stage_hours: 24
```

---

## Completing Generated Anti-Corruption Layer (ACL) Code

`ACLGenerator` inspects public functions via Python AST and generates a compilable Python adapter class (`<ModuleName>ACL`) containing:
1. **Feature-flag routing**: Checks `use_modern_<module>_<operation>` (compatible with PVS `FeatureFlagService`).
2. **Security boundary hook (`authorize_fn`)**: Optional authorization callback invoked before both legacy and modern calls.
3. **Translation hooks (`_translate_to_modern` / `_translate_to_legacy`)**: Fill in schema/field mappings so legacy callers remain untouched while the modern service uses clean domain models.
4. **Automatic legacy fallback**: If `fallback_to_legacy_on_error=True` (default), exceptions in the modern path transparently fall back to the legacy implementation.

---

## Routing Strategies & Progressive Monitoring

`RoutingStrategyPlanner` supports three routing patterns:
- **`feature_flag`** (recommended for in-process modular monoliths): Consistent SHA-256 user bucketing via `should_route_to_modern(user_id, pct)` or `FeatureFlagService`.
- **`reverse_proxy`** (NGINX / Envoy): Weighted `split_clients` upstream routing with instant `0%` reload rollback.
- **`api_gateway`** (Kong / AWS API Gateway): Route-level canary weight rules per `/api/<module>/*`.

### What to Monitor During Each 24h Rollout Stage

1. **Error rate** (`HTTP 5xx` / uncaught exceptions) ≤ legacy baseline
2. **Latency** (`p50`, `p95`, `p99`) within `+10%` of legacy baseline
3. **Data parity** (zero shadow/audit mismatch errors between legacy and modern outputs)
4. **Guardrail SLO burn rate** & customer support tickets

---

## Usage Example

```python
import asyncio
from pathlib import Path
from product_value_system.agents import BrownfieldAgent

async def main() -> None:
    agent = BrownfieldAgent(config="config/brownfield.yaml")
    report = await agent.analyze_repository(Path("."))
    for plan in report.strangler_plans:
        print(plan.target_module.name, plan.priority_score, plan.total_estimated_effort_hours)
        print(plan.routing_strategy)

asyncio.run(main())
```

---

## Rollback

- **Disable planning**: Set `brownfield.strangler_fig.enabled: false` in [config/brownfield.yaml](../config/brownfield.yaml).
- **Production traffic rollback**: Set the module feature flag (or proxy/gateway canary weight) to `0%` to return 100% of traffic immediately to the legacy implementation.

---

## Related Documentation

- [Brownfield Git History Analysis](git-history-analysis.md)
- [Progressive Delivery & Feature Flags](progressive-delivery.md)
- [SLO & Monitoring Guide](slo-guide.md)
- [Architecture & Diagrams](ARCHITECTURE.md)
