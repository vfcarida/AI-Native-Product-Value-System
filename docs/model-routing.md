# Complexity-Based Model Routing Guide (EVO-026)

The Product Value System routes LLM requests dynamically across **LOW**, **MEDIUM**, and **HIGH** complexity model tiers—reducing API spend by 55–80% while preserving output quality via automated validation and higher-tier fallback.

---

## Architecture

1. **Complexity Classification (`ComplexityClassifier`)**: Extracts 6 features (`input_length` via `tiktoken`, task keywords, `schema_driven` determinism, `creativity`, `reasoning_depth`, `code_complexity`) and classifies prompts into `LOW (<0.33)`, `MEDIUM (<0.67)`, or `HIGH (>=0.67)`.
2. **Routing Policy & Model Router (`RoutingPolicy`, `ModelRouter`)**: Selects the cheapest configured model within the classified tier (honoring optional `preferred_provider`, `user_override_model`, `force_tier`, and `shadow_mode`) and builds an ordered higher-tier fallback chain.
3. **Quality Validation & Automatic Fallback (`QualityValidator`, `FallbackHandler`)**: Validates responses using JSON Schema compliance, required keys, truncation checks, or an optional LLM-as-judge callable; automatically escalates to fallback models on quality or model failure.
4. **Transparent Integration (`RoutedModelAdapter`, `Harness`)**: Works either as a drop-in `RoutedModelAdapter` or directly inside `Harness(..., model_router=router, adapter_factory=..., quality_validator=...)` alongside budget guards and OpenTelemetry GenAI spans.

---

## Configuration (`config/routing.yaml`)

See [`config/routing.yaml`](../config/routing.yaml). Supports hot-reload via `router.reload()` and safe rollback controls:

- **Disable routing**: `enabled: false`
- **Shadow mode (log only)**: `shadow_mode: true`
- **Force safe frontier tier**: `force_tier: high`

---

## Usage Example

```python
from product_value_system.adapters.base_adapter import RoutedModelAdapter
from product_value_system.routing import ModelRouter, QualityValidator

router = ModelRouter.from_yaml("config/routing.yaml")
validator = QualityValidator(method="schema_compliance", required_keys=["functions"])

routed_adapter = RoutedModelAdapter(
    router=router,
    adapter_factory=my_adapter_factory,
    quality_validator=validator,
)

# LOW complexity -> claude-3-haiku / gpt-3.5-turbo; auto-escalates on schema failure
response = await routed_adapter.invoke(
    [{"role": "user", "content": "Extract function names from this code as JSON"}]
)

# Force a specific model for a high-stakes call
override_resp = await routed_adapter.invoke(
    [{"role": "user", "content": "Evaluate migration strategy"}],
    user_override_model="claude-3-opus-20240229",
)

print(router.get_metrics())  # decisions_by_tier, fallback_rate, cost_savings_pct
```

---

## Installation

```bash
pip install -e ".[routing]"
```

See also: [README](../README.md), [LLM Token Caching](token-caching.md), [Cost Management](COST_MANAGEMENT.md).
