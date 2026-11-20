# Baseline Validation Guide (EVO-019)

Running an A/B experiment on a noisy, underpowered, or drifting baseline produces misleading p-values and risky product launches. The `product_value_system.validation` package enforces statistical rigor **before** experiments begin.

---

## Validation Pillars

Every `ValidationGate.validate_baseline()` run evaluates three statistical and operational dimensions and rolls them into an interpretable **0–100 Baseline Quality Score**:

### 1. Statistical Quality (`StatisticalValidator`)

| Check | Default Threshold | Severity | Why It Matters |
| :--- | :---: | :---: | :--- |
| **Sample Size** | `n >= 30` | Error (blocking) | Tiny baselines (`n < 30`) fail Central Limit Theorem assumptions |
| **Coefficient of Variation (CV)** | `CV = std / mean <= 0.30` | Error (blocking) | High variance inflates false positives and masks real effects |
| **Constant Values** | `len(set(values)) > 1` | Error (blocking) | Zero variance indicates broken telemetry or stubbed data |
| **Z-Score Outliers** | `|z| > 3.0` | Warning | Extreme spikes (outages, malformed payloads) distort sample means |
| **Normality** | Shapiro-Wilk / KS `p > 0.05` | Warning | Skewed baselines suggest using non-parametric tests (Mann-Whitney U) |

### 2. Statistical Power Analysis (`PowerAnalyzer`)

Computes:
- **Minimum Detectable Effect (MDE)**: Smallest absolute and relative (`%`) improvement detectable at `alpha = 0.05` and `target_power = 0.80` for the current baseline sample size.
- **Achieved Power**: Exact power via the non-central $t$-distribution (`scipy.stats.nct`) for the requested effect size (`effect_size` or `effect_size_percent`).
- **Recommended Sample Size**: Total samples needed across both arms to hit `target_power` (`0.80`), with actionable deficit suggestions (`"Collect N more samples to reach power=0.80"`).

Supports both continuous metrics (`latency_ms`, `cost_usd`, `tokens`) and binary rates (`success_rate`, `conversion_rate`).

### 3. Temporal & Operational Stability (`StabilityChecker`)

- **Distribution Drift**: Splits baseline observations chronologically into first half vs. second half and runs a two-sided Mann-Whitney U test (`p < 0.05` flags drift).
- **Incidents**: Queries `incident_tracker` for `critical`/`high` incidents overlapping the baseline window.
- **Deployments**: Queries `deploy_tracker` for production releases during baseline collection.

---

## CLI Usage

```bash
# Validate a saved baseline JSON/YAML dataset
pvs experiment validate-baseline baseline.json --effect-size 10

# Extract & validate directly from an OpenTelemetry trace JSON/JSONL export
pvs experiment validate-baseline \
  --traces traces/payment.json \
  --metric latency_ms \
  --effect-size 10 \
  --output baseline_report.json

# Non-strict mode (allows proceeding with warnings when no hard errors exist)
pvs experiment validate-baseline baseline.json --non-strict
```

---

## Auto-Validating Experiments in Python

Gate `ABTest` startups automatically with either `ABTest.start(baseline=..., auto_validate=True)` or `ValidatedExperimentRunner`:

```python
from product_value_system.experiment import ABTest
from product_value_system.validation import (
    BaselineData,
    ValidatedExperimentRunner,
    ValidationGate,
)

gate = ValidationGate()
runner = ValidatedExperimentRunner(validation_gate=gate)

# Raises ValueError with detailed summary if baseline fails strict gate
gate_result = runner.start_experiment(
    ab_test,
    baseline=baseline_data,
    effect_size_percent=10.0,
    auto_validate=True,
    strict_validation=True,
)
print(f"Baseline Quality Score: {gate_result.quality_score}/100")
```

---

## Related Documentation

- [Experiment Design Guide](experiment-design.md)
- [A/B Testing Best Practices](ab-testing.md)
- [Automated Drift Detection](drift-detection.md)
- [SLO & Error Budget Guide](slo-guide.md)
