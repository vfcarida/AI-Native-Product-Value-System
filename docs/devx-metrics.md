# Developer Experience (DevX) & SPACE Metrics Guide (EVO-032)

DevX tracking quantifies engineering velocity, AI-assist effectiveness, and developer well-being using the **SPACE framework** (Satisfaction, Performance, Activity, Communication, Efficiency) alongside **DORA** delivery metrics, with strict anonymization, opt-out, and non-punitive governance.

---

## SPACE Framework Dimensions

| Dimension | Signals Tracked | Normalization (0.0 – 1.0) |
|---|---|---|
| **S — Satisfaction & Well-being** | Voluntary bi-weekly pulse survey ratings (`tool_satisfaction`, `cognitive_load`, `flow_state`) | Mean `tool_satisfaction / 5.0` |
| **P — Performance** | Stage task success rate & AI-generated output acceptance rate | Average of task success rate and AI acceptance rate |
| **A — Activity** | Completed tasks across Discovery, Spec, Build, Review, Deploy | `completed_tasks_7d / weekly_target` (capped at `1.0`) |
| **C — Communication** | PR review turnaround time (`devx_pr_review_turnaround_seconds`) | `1.0` at `<= 1h`, scaling down to `0.3` at `>= 24h` |
| **E — Efficiency & Flow** | Stage cycle time & DORA lead time (`devx_cycle_time_seconds`) | `1.0` at `<= 5m`, scaling down to `0.3` at `>= 30m` |

### DORA Delivery Metrics

`CycleTimeTracker.compute_dora_metrics()` computes all four *Accelerate* metrics:
- **Deployment Frequency** (`deployments / day`)
- **Lead Time for Changes** (mean, p50, p95 end-to-end task duration)
- **Change Failure Rate** (`failed_deployments / total_deployments`)
- **Mean Time to Restore (MTTR)** (average recovery duration for failed deployments)

Core modules:
- [src/product_value_system/metrics/devx_tracker.py](../src/product_value_system/metrics/devx_tracker.py) — `DevXTracker`, Prometheus counters/histograms/gauges, AI effectiveness metrics, opt-out & transparency summary
- [src/product_value_system/metrics/cycle_time_tracker.py](../src/product_value_system/metrics/cycle_time_tracker.py) — `CycleTimeTracker`, `StageTiming`, `DORAMetrics`
- [src/product_value_system/metrics/space_calculator.py](../src/product_value_system/metrics/space_calculator.py) — `SPACECalculator`, `SPACEScores`, `LocalDevXQueryAdapter`, `HttpPrometheusQueryClient`
- [src/product_value_system/metrics/satisfaction_survey.py](../src/product_value_system/metrics/satisfaction_survey.py) — `SatisfactionSurvey`, `AggregatedSurveyReport`, PII-scrubbed feedback
- [config/metrics.yaml](../config/metrics.yaml) — DevX configuration
- [dashboards/devx_dashboard.json](../dashboards/devx_dashboard.json) & [monitoring/grafana/dashboards/devx_dashboard.json](../monitoring/grafana/dashboards/devx_dashboard.json) — Grafana dashboard

---

## Configuration (`config/metrics.yaml`)

```yaml
devx:
  enabled: true
  dimensions:
    satisfaction: true
    performance: true
    activity: true
    communication: true
    efficiency: true
  surveys:
    enabled: true
    frequency_days: 14
  ai_assist:
    track_usage: true
    track_acceptance: true
    track_corrections: true
  anonymize: true
  aggregate_only: true
  retention_days: 90
```

---

## Usage Examples

### 1. Value Stream Orchestrator Instrumentation

```python
from product_value_system.metrics import DevXTracker, SPACECalculator
from product_value_system.orchestrator import ValueStreamOrchestrator

tracker = DevXTracker.from_yaml("config/metrics.yaml")
orchestrator = ValueStreamOrchestrator(
    agents=agents,
    evaluation_pipeline=eval_pipeline,
    policy_engine=policy_engine,
    checkpoint_manager=checkpoint_manager,
    artifact_root=artifact_root,
    devx_tracker=tracker,
)
await orchestrator.execute_run(problem)

space = await SPACECalculator(tracker=tracker).compute_space_scores()
print("SPACE overall:", space["overall"])
print("AI effectiveness:", tracker.get_ai_effectiveness())
print("DORA tier:", tracker.get_dora_metrics().performance_tier)
```

### 2. Voluntary Satisfaction Pulse Survey

```python
from product_value_system.metrics import SatisfactionSurvey

survey = SatisfactionSurvey()
print(survey.render_prompt())
survey.submit_response(
    {
        "tool_satisfaction": 4,
        "cognitive_load": 2,
        "flow_state": 4.5,
        "feedback": "Faster test feedback loops would help.",
    },
    developer_id="alice@example.org",  # Hashed deterministically; raw ID never stored
)
report = survey.aggregate()
tracker.record_satisfaction("tool_satisfaction", report.rating_averages["tool_satisfaction"])
```

---

## Grafana Dashboard Usage

Import [dashboards/devx_dashboard.json](../dashboards/devx_dashboard.json) (or run `docker-compose -f docker-compose.monitoring.yml up`, which auto-loads `monitoring/grafana/dashboards/devx_dashboard.json`).

Panels included:
1. **SPACE Framework Dimension Scores** (`devx_space_score`) — live gauges for all 5 dimensions + overall score
2. **Developer Satisfaction Pulse Scores** (`avg_over_time(devx_satisfaction_score[7d])`)
3. **Task Cycle Time by Stage** (p50 & p95 from `devx_cycle_time_seconds_bucket`)
4. **AI Assist Acceptance Rate by Agent** (`devx_ai_usage_total`)
5. **Completed Tasks by Stage** (`devx_tasks_completed_total`)
6. **DORA Delivery** (`devx_deployments_total` & `devx_pr_review_turnaround_seconds_bucket`)

---

## Privacy Policy & Ethical Principles

1. **Non-Punitive Purpose**: DevX metrics exist strictly to eliminate tooling bottlenecks and measure AI workflow improvements — **never** for individual performance reviews or stack ranking.
2. **Anonymization**: Developer IDs are hashed with SHA-256 (`dev_<hash>`) and email addresses inside free-text survey comments are automatically replaced with `[REDACTED_EMAIL]`.
3. **Voluntary Opt-Out**: Any engineer can opt out at any time via `tracker.opt_out(developer_id)` or `survey.opt_out(developer_id)`.
4. **Aggregate-Only Reporting & Transparency**: Team dashboards only surface aggregated cohort metrics (`aggregate_only: true`), while individuals may inspect their own anonymized statistics via `tracker.get_developer_summary(developer_id)`.
5. **Retention & Rollback**: Default metric retention is 90 days. Set `devx.enabled: false` in [config/metrics.yaml](../config/metrics.yaml) for instant rollback.

---

## Related Documentation

- [SLO & Monitoring Guide](slo-guide.md)
- [Grafana Dashboards](dashboards.md)
- [OpenTelemetry GenAI Conventions](otel-genai-conventions.md)
- [Cost Management](COST_MANAGEMENT.md)
