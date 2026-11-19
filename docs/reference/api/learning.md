# API Reference: Learning & Outcome Measurement

**Module**: `product_value_system.learning`  
**Source**: [src/product_value_system/learning/](../../../src/product_value_system/learning/)

Implements the **Learn stage** of the AI-Native Product Value System: querying post-deployment production metrics, evaluating statistical significance and guardrails against baseline hypotheses, attributing LLM/agent development costs to measured improvements, triggering alerts on regressions, and feeding structured learnings back into Discovery.

---

## Analytics Adapters

**File**: `src/product_value_system/learning/analytics_adapter.py`

### `AnalyticsAdapter` (Protocol)

```python
@runtime_checkable
class AnalyticsAdapter(Protocol):
    async def query_metric(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        filters: dict[str, Any] | None = None,
    ) -> MetricResult: ...

    async def query_timeseries(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str = "daily",
        filters: dict[str, Any] | None = None,
    ) -> TimeseriesResult: ...
```

### Implementations

- **`SQLAnalyticsAdapter(connection_string)`**: Supports SQLite natively (`sqlite:///analytics.db` or `:memory:`) and PostgreSQL/MySQL asynchronously via SQLAlchemy (`postgresql+asyncpg://...`). Computes mean, sample size, sample standard deviation, and 95% t-distribution confidence intervals. Validates filter keys against `^[a-zA-Z_][a-zA-Z0-9_]*$` to prevent SQL injection.
- **`PrometheusAnalyticsAdapter(base_url, timeout=15.0, transport=None)`**: Queries Prometheus `/api/v1/query` and `/api/v1/query_range` via `httpx.AsyncClient`. Call `await adapter.close()` when finished.
- **`MockAnalyticsAdapter(mock_data, *, baseline_data=None, sample_size=1000, ci_half_width_ratio=0.05, seed=42)`**: Deterministic in-memory mock adapter for unit tests, integration tests, and local CLI demonstrations.

See [config/analytics_adapters.yaml](../../../config/analytics_adapters.yaml) for configuration options.

---

## `OutcomeTracker` & `OutcomeMeasurement`

**File**: `src/product_value_system/learning/outcome_tracker.py`

```python
tracker = OutcomeTracker(analytics_adapter, cost_attributor=None)
tracker.register_deployment(
    outcome_id="outcome-acme-payment",
    deployment_date=datetime(2026, 10, 15, tzinfo=UTC),
    outcome_hypothesis=outcome_dict,
    development_cost_usd=14.50,
    stage_costs={"outcome_hypothesis": 3.20, "specification": 4.80, "build": 6.50},
)
measurement = await tracker.measure_outcome(
    outcome_id="outcome-acme-payment",
    measurement_window_days=30,
    baseline_window_days=30,
)
```

- Supports both canonical `outcome.schema.json` (`success_criteria.primary_metric`, `baseline`, `target`, `guardrails`) and shorthand `primary_metric` dictionaries.
- Automatically determines metric polarity (`higher_is_better = target >= baseline`), handling both maximize metrics (e.g., retention) and minimize metrics (e.g., p95 latency).
- Computes two-sided t-test p-values via `scipy.stats.t` and classifies outcomes as `ACHIEVED`, `PARTIAL_SUCCESS`, `NOT_ACHIEVED`, or `REGRESSED`.
- Evaluates guardrail metrics (`direction`: `above` or `below`) and synthesizes quantitative learnings and recommendations.

---

## `CostAttributor` & `CostAttributionReport`

**File**: `src/product_value_system/learning/attribution.py`

Links cumulative LLM/agent development costs (with per-stage breakdown) and ongoing monthly operational costs to achieved metric improvement (`cost_per_unit_improvement = development_cost_usd / improvement` when `improvement > 0`, otherwise `None`).

---

## `MeasurementAgent`

**File**: `src/product_value_system/learning/measurement_agent.py`

Subclasses `BaseAgent` to execute the Learn stage inside `ValueStreamOrchestrator` or standalone:

```python
agent = MeasurementAgent(
    outcome_tracker=tracker,
    model_adapter=None,  # Optional LLM for qualitative insight synthesis
    name="measurement-agent",
)
result = await agent.execute(
    input_artifact={"outcome_id": "outcome-acme-payment", "measurement_window_days": 30},
    harness=harness,
    context=context,
)
```

When `ValueStreamOrchestrator` is configured with `analytics_adapter` (or `outcome_tracker`) and invoked with `config={"enable_measurement": True}`, it runs the Learn (`measure`) stage after the 10 standard stages, writes `measurement.yaml` and `discovery_feedback.yaml`, enriches `outcome.yaml` with `measurement` (valid against `schemas/outcome.schema.json`), and emits a `learning.captured` event. Pass `defer_measurement=True` to schedule future measurement without immediate analytics queries.

---

## `FeedbackLoop`

**File**: `src/product_value_system/learning/feedback_loop.py`

- **`await evaluate_measurement(measurement) -> list[FeedbackAlert]`**: Generates `critical` alerts on regressions (`REGRESSED`) and guardrail breaches, `warning` alerts on missed targets (`NOT_ACHIEVED`), records OpenTelemetry `learning.alert` span events, and invokes `alert_callback` (sync or async).
- **`record_feedback(measurement) -> DiscoveryFeedbackItem`**: Converts measurement into a `DiscoveryFeedbackItem` and optionally persists YAML history to `feedback_store_path`.
- **`await run_measurement_cycle(outcome_ids=None, measurement_window_days=30)`**: Evaluates all (or selected) registered deployments in a continuous feedback sweep.
- **`build_discovery_context(limit=5) -> dict[str, Any]`**: Summarizes `winning_patterns`, `anti_patterns`, `open_recommendations`, and `historical_outcomes` for future `DiscoveryAgent` runs.

---

## CLI Usage

```bash
# Measure an outcome with deterministic mock analytics
pvs measure-outcome --outcome-id outcome-acme-payment --measurement-window 30 --use-mock-analytics

# Measure against a SQLite analytics database and save YAML report
pvs measure-outcome \
  --outcome-id outcome-acme-payment \
  --outcome-file examples/learning/outcome_measurement.yaml \
  --db-url sqlite:///analytics.db \
  --output artifacts/measurement.yaml
```
