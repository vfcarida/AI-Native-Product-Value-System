# API Reference: Observability

## Module: `product_value_system.observability`

**Source**: [`src/product_value_system/observability/`](../../../src/product_value_system/observability/)

---

### Tracing (`tracing.py`)

**Source**: [`src/product_value_system/observability/tracing.py`](../../../src/product_value_system/observability/tracing.py)

- `configure_tracing(service_name, service_version, otlp_endpoint, console_export)` — Configures OpenTelemetry `TracerProvider` with console and/or OTLP gRPC exporters.
- `@traced(span_name=None, attributes=None, record_exception=True)` — Decorator instrumenting synchronous and asynchronous functions with automatic `pvs.run_id` propagation and PII sanitization.
- `set_run_context(run_id)` / `clear_run_context()` — Manages `CURRENT_RUN_ID` context variable.

---

### GenAI Semantic Conventions (`genai_conventions.py`)

**Source**: [`src/product_value_system/observability/genai_conventions.py`](../../../src/product_value_system/observability/genai_conventions.py)

- `GenAIAttributes` — Standard OpenTelemetry GenAI attribute constants (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cost_usd`).
- `instrument_model_call(span, system, model, operation, input_tokens, output_tokens, finish_reason, response_id, cost_usd)`

---

### Cost Tracking (`cost_tracker.py`)

**Source**: [`src/product_value_system/observability/cost_tracker.py`](../../../src/product_value_system/observability/cost_tracker.py)

- `CostTracker(custom_pricing=None)` — Computes per-call `CostBreakdown` and cumulative run summaries across OpenAI, Anthropic, Google, and custom models.

---

### PII & Secret Sanitization (`sanitization.py`, `sanitized_tracing.py`)

**Sources**:
- [`src/product_value_system/observability/sanitization.py`](../../../src/product_value_system/observability/sanitization.py)
- [`src/product_value_system/observability/sanitized_tracing.py`](../../../src/product_value_system/observability/sanitized_tracing.py)

- `sanitize_string(value)` / `sanitize_attributes(attributes)` — Redacts emails, phones, SSNs, credit cards, and sensitive attribute keys.
- `SanitizingSpanProcessor` / `setup_sanitized_tracing(service_name)` — Redacts secrets from OpenTelemetry span attributes prior to export.
