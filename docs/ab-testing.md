# A/B Testing Best Practices (EVO-016)

This guide covers the statistical methods, privacy protections, and operational practices built into `product_value_system.experiment.ABTest`.

---

## Consistent Randomized Assignment

`ABTest.assign_variant(user_id)` hashes `f"{experiment_id}:{user_id}"` with SHA-256 and assigns users to `Variant.CONTROL` or `Variant.TREATMENT` on a deterministic 50/50 split:

- **Stable**: The same user always sees the same variant across sessions (`<1ms` per lookup).
- **Independent across experiments**: Including `experiment_id` prevents cross-experiment correlation.
- **Privacy-preserving**: Raw `user_id` values are never stored in `ABTest.data` or serialized state files — only aggregated numeric observations per variant are retained.

---

## Choosing a Statistical Test

`ABTest.analyze(test_type=...)` supports three complementary inference methods:

### 1. Welch's Two-Sample t-Test (`test_type="ttest"`, default)

- **Best for**: Continuous metrics (revenue per user, latency, session duration, evaluation quality score).
- **Behavior**: Does not assume equal variance between control and treatment (`equal_var=False`), outputs two-sided `p_value`, relative `lift`, and 95% confidence interval `(ci_lower, ci_upper)`.

### 2. Pearson Chi-Square Test (`test_type="chi_square"`)

- **Best for**: Binary conversion rates (`0` / `1` observations such as checkout completion, activation, or retention).
- **Behavior**: Builds the $2 \times 2$ contingency table of successes and failures and evaluates independence with `scipy.stats.chi2_contingency`.

### 3. Bayesian A/B Analysis (`test_type="bayesian"`)

- **Best for**: Continuous monitoring and intuitive stakeholder reporting ("97.8% probability treatment beats control").
- **Behavior**:
  - Binary (`0`/`1`) data uses exact conjugate `Beta(1 + successes, 1 + failures)` posterior draws.
  - Continuous data uses Normal-Normal posterior simulation (`N(mean, se^2)`).
  - Outputs `bayesian_prob_treatment_beats_control` and a 95% equal-tailed `bayesian_credible_interval`.

---

## Statistical vs. Practical Significance

A statistically significant result (`p < 0.05`) is **not** automatically worth shipping:

- With very large sample sizes, a `+0.2%` lift can achieve `p < 0.001` while failing to cover maintenance costs.
- `ABTest` therefore enforces **both** `statistically_significant` AND `practically_significant` (`lift >= minimum_detectable_effect`) before issuing an **`adopt`** recommendation.

---

## Common Pitfalls & Mitigations

| Pitfall | Risk | Built-In Mitigation |
| :--- | :--- | :--- |
| **Peeking at `p < 0.05`** | Inflates false positive rate from 5% to 25%+ | `should_stop_early()` enforces stricter `alpha = 0.01` AND `lift >= MDE` |
| **Underpowered experiments** | Misses real wins or reports noisy lifts | `PowerAnalyzer` computes required sample size upfront |
| **Ignoring secondary harm** | Lifts conversion while breaking latency/revenue | `GuardrailMetric` checks automatically abort on violation |
| **Short weekday-only runs** | Weekend traffic bias invalidates lift | `estimate_duration_days()` enforces a minimum 7-day window |
| **PII leakage in exports** | Compliance / GDPR violation | `ABTest` hashes `user_id` on assignment and stores only numeric aggregates |

---

## Related Documentation

- [Experiment Design Guide](experiment-design.md)
- [Progressive Delivery Guide](progressive-delivery.md)
- [Flag Management Guide](flag-management.md)
