# Experiment Design Guide (EVO-016)

Before investing engineering effort into a full production build, validate outcome hypotheses with controlled A/B experiments grounded in Cohen (1988) power analysis and Kohavi et al. (2020) *Trustworthy Online Controlled Experiments*.

---

## When to Run an Experiment

Run an experiment whenever:

- **Causal impact is uncertain**: A new prompt, UX flow, or pricing change could improve or degrade the primary metric.
- **High implementation cost**: Validating with a lightweight experiment avoids shipping unvalidated features.
- **Guardrail risk exists**: Changes might lift conversion while harming latency, error rate, or revenue.

Skip formal A/B tests for urgent security patches, bug fixes, or legal compliance changes (use [Progressive Delivery](progressive-delivery.md) dark launches and canaries instead).

---

## Generating an Experiment Design from an Outcome Hypothesis

`ExperimentDesignAgent` takes any outcome artifact (matching [`schemas/outcome.schema.json`](../schemas/outcome.schema.json)) and outputs a schema-validated [`ExperimentDesign`](../schemas/experiment_design.schema.json) blueprint:

```bash
# Compute sample size directly via power analysis
pvs experiment sample-size --baseline 0.45 --mde 0.10 --metric-type proportion

# Design an A/B experiment from an outcome hypothesis YAML
pvs experiment design artifacts/runs/problem-001/outcome.yaml --output experiment_design.yaml

# Analyze saved experiment observations
pvs experiment analyze experiment_state.json --test-type ttest
```

Programmatically in Python:

```python
from product_value_system.agents import ExperimentDesignAgent
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness

agent = ExperimentDesignAgent(confidence_level=0.95, statistical_power=0.80)
harness = Harness(context=ExecutionContext(run_id="exp-01", agent_name=agent.name))
result = await agent.execute(outcome_artifact, harness, harness.context)
design = result.output_artifact
```

Or automatically inside `ValueStreamOrchestrator`:

```python
run = await orchestrator.execute_run(
    problem=problem,
    config={"enable_experiment_design": True},
)
```

---

## Power Analysis & Sample Size

`PowerAnalyzer` computes per-variant sample sizes using `scipy.stats.norm`:

- **Continuous metrics (Two-sample t-test)**:
  $$n = \left\lceil 2 \cdot (z_{\alpha/2} + z_{\beta})^2 \cdot \frac{\sigma^2}{\delta^2} \right\rceil$$
  Default parameters: $\alpha = 0.05$ (`confidence_level = 0.95`), power $1 - \beta = 0.80$, $\sigma = 0.10 \times \text{baseline}$ (overridable via `std_dev`).
- **Conversion / binary rate metrics (Two-proportion chi-square / z-test)**:
  Uses pooled variance under $H_0$ and unpooled variance under $H_1$ for exact proportion power sizing.
- **Duration estimation**:
  Clamped to `7–30` days (`sample_size / (daily_traffic * 0.5)`) to cover full weekly seasonality cycles while preventing indefinite runs.

---

## Guardrail Metrics & Stopping Criteria

Every `ExperimentDesign` defines **guardrail metrics** (`GuardrailMetric`) and **stopping criteria**:

1. **Guardrail Violation (Highest Priority)**: Immediately aborts the experiment (`ExperimentStatus.ABORTED`) and invokes `alert_callback` if a guardrail crosses its threshold (e.g., revenue drops `>5%` or error rate increases `>1%`).
2. **Maximum Duration**: Completes the experiment once `elapsed_days >= max_duration_days`.
3. **Early Stopping**: Stops early (`ExperimentStatus.STOPPED_EARLY`) only when **both** stricter statistical significance (`p < 0.01`) **and** practical significance (`lift >= MDE`) hold.

---

## Interpreting Recommendations

| Recommendation | Condition | Action |
| :--- | :--- | :--- |
| **`adopt`** | No guardrail violations + statistically significant + `lift >= MDE` | Roll out via [Progressive Delivery](progressive-delivery.md) |
| **`reject`** | Guardrail violated **OR** statistically significant lift is below MDE | Do not ship; feed learnings back to Discovery |
| **`continue`** | No guardrail violations + not yet statistically significant | Collect more samples up to `max_duration_days` |

---

## Related Documentation

- [A/B Testing Best Practices](ab-testing.md)
- [Progressive Delivery Guide](progressive-delivery.md)
- [SLO & Error Budget Guide](slo-guide.md)
