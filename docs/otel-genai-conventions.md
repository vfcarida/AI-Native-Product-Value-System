# OpenTelemetry GenAI Semantic Conventions Guide (EVO-021)

The Product Value System standardizes LLM telemetry on **OpenTelemetry GenAI Semantic Conventions (v1.28+)** so traces and metrics interoperate out of the box with Jaeger, Grafana, Honeycomb, Traceloop, Azure Monitor, AWS X-Ray, and Google Cloud Trace.

---

## Standard Attributes (`gen_ai.*`)

| Attribute | Example | Description |
| :--- | :--- | :--- |
| `gen_ai.system` | `"anthropic"`, `"openai"`, `"vertex_ai"` | Model provider identifier (auto-inferred via `infer_genai_system`) |
| `gen_ai.operation.name` | `"chat"`, `"completion"`, `"embedding"`, `"rerank"` | Standard operation type |
| `gen_ai.request.model` | `"claude-sonnet-4"` | Requested model name |
| `gen_ai.response.model` | `"claude-sonnet-4"` | Actual serving model returned by provider |
| `gen_ai.response.id` | `"msg_abc123"` | Provider response identifier |
| `gen_ai.response.finish_reason` | `"stop"`, `"length"`, `"tool_calls"` | Completion stop reason |
| `gen_ai.usage.prompt_tokens` | `150` | Input/prompt token count |
| `gen_ai.usage.completion_tokens` | `50` | Output/completion token count |
| `gen_ai.usage.total_tokens` | `200` | Combined token count |
| `gen_ai.usage.cost_usd` | `0.0025` | Attributed operation cost in USD |
| `gen_ai.request.temperature` | `0.7` | Sampling temperature |
| `gen_ai.request.max_tokens` | `1024` | Max output token cap |
| `gen_ai.embedding.dimensions` | `1536` | Vector dimensions for `embedding` operations |

---

## Standard Span Events

- **`gen_ai.content.prompt`**: Chat messages (`gen_ai.prompt.0.role`, `gen_ai.prompt.0.content`) or raw `gen_ai.prompt` string.
- **`gen_ai.content.completion`**: Generated response (`gen_ai.completion`).
- **`gen_ai.tool.call`**: Tool invocations (`gen_ai.tool.name`, `gen_ai.tool.input`).
- **`gen_ai.tool.result`**: Tool execution outputs (`gen_ai.tool.name`, `gen_ai.tool.output`).

Set `GenAITracer(tracer, sanitize_content=True)` (used by default inside `Harness.invoke_model`) to redact secrets/PII from content events automatically.

---

## Standard GenAI Metrics (`GenAIMetrics`)

| Metric Name | Instrument | Unit | Key Labels |
| :--- | :--- | :---: | :--- |
| `gen_ai.client.token.usage` | Histogram | `tokens` | `gen_ai.system`, `gen_ai.request.model`, `gen_ai.token.type` |
| `gen_ai.client.operation.duration` | Histogram | `seconds` | `gen_ai.system`, `gen_ai.operation.name`, `gen_ai.response.finish_reason` |
| `gen_ai.client.operation.error` | Counter | `errors` | `gen_ai.system`, `error.type` |
| `gen_ai.client.operation.cost` | Counter | `USD` | `gen_ai.system`, `gen_ai.request.model` |

---

## Usage Examples

### Chat Completion + Tool Calls + Automatic Metrics

```python
from opentelemetry import metrics, trace
from product_value_system.observability import GenAIMetrics, GenAITracer

genai_metrics = GenAIMetrics(metrics.get_meter(__name__))
genai_tracer = GenAITracer(trace.get_tracer(__name__), metrics=genai_metrics)

with genai_tracer.trace_chat_completion(
    operation_name="draft_specification",
    model="claude-sonnet-4",
    messages=[{"role": "user", "content": "Draft API spec"}],
    temperature=0.3,
    max_tokens=1024,
) as span:
    span.record_tool_call("schema_lookup", '{"schema": "specification"}')
    span.record_tool_result("schema_lookup", '{"status": "ok"}')
    span.record_completion(
        response_id="msg_123",
        completion="Specification draft...",
        prompt_tokens=150,
        completion_tokens=420,
        cost_usd=0.0068,
    )
```

### Text Completion, Embeddings, and Reranking

```python
with genai_tracer.trace_text_completion("summarize", "gpt-4o", "Summarize:") as span:
    span.record_completion("cmpl_1", "Summary.", prompt_tokens=10, completion_tokens=4)

with genai_tracer.trace_embedding("embed_doc", "text-embedding-3-small", "hello") as span:
    span.record_embedding(dimensions=1536, token_count=2)

with genai_tracer.trace_rerank("rerank_docs", "rerank-english-v3.0", "auth", ["doc1", "doc2"], top_n=1) as span:
    span.record_rerank(returned_count=1, token_count=30)
```

---

## Migration Guide: Old (`llm.*`) to Standard (`gen_ai.*`)

| Deprecated Custom Attribute | Standard OpenTelemetry Attribute |
| :--- | :--- |
| `llm.model` | `gen_ai.request.model` |
| `llm.provider` | `gen_ai.system` |
| `llm.tokens` | `gen_ai.usage.total_tokens` |
| `llm.prompt_tokens` | `gen_ai.usage.prompt_tokens` |
| `llm.completion_tokens` | `gen_ai.usage.completion_tokens` |
| `llm.temperature` | `gen_ai.request.temperature` |
| `llm.max_tokens` | `gen_ai.request.max_tokens` |
| `llm.response_id` | `gen_ai.response.id` |
| `llm.finish_reason` | `gen_ai.response.finish_reason` |
| `llm.operation` | `gen_ai.operation.name` |
| `llm.cost` | `gen_ai.usage.cost_usd` |

Use `migrate_attributes()` or `CompatibilityTracer(genai_tracer)` during gradual migration to emit `DeprecationWarning` while exporting compliant `gen_ai.*` spans.

---

## OTEL Collector & Grafana Dashboard

- Collector pipeline config: [`monitoring/otel-collector-config.yml`](../monitoring/otel-collector-config.yml)
- Provisioned Grafana dashboard: [`monitoring/grafana/dashboards/genai_overview.json`](../monitoring/grafana/dashboards/genai_overview.json)
- Related guides: [SLO Guide](slo-guide.md), [Grafana Dashboards](dashboards.md), [Monitoring Operations](operations/monitoring.md)
