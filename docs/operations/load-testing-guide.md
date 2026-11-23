# Load Testing & Capacity Planning Guide (k6)

This guide covers executing the k6 load testing suite, detecting performance regressions in CI/CD, and generating Kubernetes/FinOps capacity models for the AI-Native Product Value System.

---

## Safety & Isolation Invariants

- **Never Target Production**: Always execute load tests against the isolated `pvs-load-test` namespace / staging cluster.
- **Spend-Capped Test Keys**: Use dedicated test LLM API keys capped at `$100/month` (or deterministic Mock mode for pure control-plane throughput testing).
- **Synthetic, PII-Free Fixtures**: All project payloads in [tests/load/fixtures/test-data.json](../../tests/load/fixtures/test-data.json) are synthetic (`seed: 42`).

---

## Load Test Scenarios

| Scenario | Script | Profile | SLO Threshold | Make Command |
|---|---|---|---|---|
| **Greenfield Workload** | [k6-greenfield.js](../../tests/load/k6-greenfield.js) | `0 -> 20 -> 100 -> 0` VUs (`25m`) | `p99 < 5s`, errors `< 1%` | `make load-test-greenfield` |
| **Brownfield Workload** | [k6-brownfield.js](../../tests/load/k6-brownfield.js) | `0 -> 15 -> 50 -> 0` VUs (`18m`) | `p99 < 10s`, errors `< 1%` | `make load-test-brownfield` |
| **Stress Test** | [k6-stress-test.js](../../tests/load/k6-stress-test.js) | `10 -> 10,000 req/s` (`30m`) | `p50 < 1s`, `p99 < 10s` | `make load-test-stress` |
| **24h Soak Test** | [k6-soak-test.js](../../tests/load/k6-soak-test.js) | `50` VUs sustained `24h` (`~10 req/s`) | `p99 < 1s`, errors `< 1%`, flat memory | `make load-test-soak` |
| **10x Spike Test** | [k6-spike-test.js](../../tests/load/k6-spike-test.js) | `10 -> 100` VUs in `10s`, recover | `p99 < 5s`, errors `< 10%` (`200`/`503`) | `make load-test-spike` |

---

## Running Load Tests

```bash
export BASE_URL="http://test.product-value-system.internal"
export TEST_API_KEY="<spend-capped-test-key>"
export LOAD_TEST_SEED="42"

# Quick 1-minute smoke check
make load-test-smoke

# Full greenfield workload with JSON summary export
k6 run tests/load/k6-greenfield.js --summary-export=results/greenfield-summary.json
```

---

## Regression Detection & Capacity Planning

```bash
# 1. Compare current summary against main-branch baseline (fails CI on >10% p99 regression)
python3 scripts/analyze-k6-results.py \
  results/greenfield-summary.json \
  results/greenfield-baseline.json \
  --slo-p99-max-ms 5000 \
  --markdown-out results/regression-report.md

# 2. Generate pod/node & monthly FinOps projections for 1k, 5k, 10k, 50k users
python3 scripts/capacity-planning.py results/stress-summary.json
```

---

## CI Performance Gate & Dashboards

- **GitHub Actions Workflow**: [.github/workflows/performance-regression.yml](../../.github/workflows/performance-regression.yml) validates every k6 script via `k6 inspect`, executes the workload when `LOAD_TEST_BASE_URL` is configured, enforces the `10%` regression ceiling, and posts Markdown summaries to PRs (override with `[skip-perf-test]` in PR title).
- **Grafana Dashboard**: Import [grafana/dashboards/load-testing-dashboard.json](../../grafana/dashboards/load-testing-dashboard.json) (`uid: pvs-load-testing`).

---

## Related Documentation

- [Production SLOs & Alerting Rules](slos.md)
- [Distributed Tracing with Jaeger](tracing-guide.md)
- [High-Latency Trace Runbook](../runbooks/trace-high-latency.md)
