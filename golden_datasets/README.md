# Golden Dataset Curation Guide

## Purpose

Golden datasets are curated, schema-validated reference samples used for automated regression evaluation of AI-Native Product Value System agents. They enable teams to:

- Detect quality regressions whenever prompts, models, policies, or agent code change
- Measure quality improvements quantitatively over time against saved baselines
- Enforce automated pass-rate evaluation gates in CI/CD before deployment
- Verify resistance to adversarial prompt injections and malformed inputs

---

## Directory Structure

```text
golden_datasets/
├── discovery/
│   └── dataset_v1.jsonl      # 50 samples (30 happy-path, 15 edge-case, 5 adversarial)
├── specification/
│   └── dataset_v1.jsonl      # 50 samples (30 happy-path, 15 edge-case, 5 adversarial)
├── build/
│   └── dataset_v1.jsonl      # 30 samples (18 happy-path, 9 edge-case, 3 adversarial)
├── review/
│   └── dataset_v1.jsonl      # 30 samples (18 happy-path, 9 edge-case, 3 adversarial)
├── brownfield/
│   └── dataset_v1.jsonl      # 20 samples (12 happy-path, 6 edge-case, 2 adversarial)
├── trajectories/
│   └── *-golden.jsonl        # 25 curated exemplar ReAct trajectories (EVO-012)
├── schema.json               # JSON Schema 2020-12 definition for sample lines
└── README.md                 # This curation guide
```

**Total v1 corpus**: **180 samples** across 5 agents + **25 golden trajectories** (`6` discovery, `6` specification, `5` build, `4` review, `4` brownfield).

---

## Coverage Dimensions (HELM Standard)

Every dataset follows a 60% / 30% / 10% distribution across three categories covering HELM's five coverage dimensions:

1. **Functional coverage (`happy-path`, 60%)**: Core capabilities, schema outputs, metrics, and workflows across diverse domains (fintech, healthcare, e-commerce, logistics, EdTech, B2B SaaS, FinOps, cybersecurity, civic/govtech, IoT, legaltech, devtools).
2. **Edge-case & temporal/demographic coverage (`edge-case`, 30%)**: Missing baselines, conflicting stakeholder goals, extreme burst/latency constraints, multi-region GDPR/LGPD residency, WCAG accessibility, RTL localization, 2G offline sync, leap-year/timezone boundaries, and legacy ERP constraints.
3. **Adversarial coverage (`adversarial`, 10%)**: Direct & indirect prompt injection (OWASP LLM-01), jailbreak overrides, secret exfiltration lures, destructive sandbox commands, and review-bypass payloads.

---

## Dataset Format (JSONL)

Each line in `dataset_v<N>.jsonl` is a standalone JSON object conforming to [`golden_datasets/schema.json`](schema.json):

```json
{
  "id": "discovery-001",
  "version": 1,
  "created": "2026-11-19T10:00:00Z",
  "validated": "2026-11-19T12:00:00Z",
  "category": "happy-path",
  "tags": ["fintech", "payments", "notifications", "mobile"],
  "input": {
    "problem": { "...valid problem.schema.json artifact..." }
  },
  "expected_output": {
    "primary_metric": {
      "name": "notification_delivery_rate",
      "unit": "percentage",
      "baseline": 60.0,
      "target": 95.0
    }
  },
  "evaluation_rubric": {
    "hypothesis_measurable": {
      "weight": 0.3,
      "criteria": "Primary metric defines name, unit, baseline, target, and measurement method"
    }
  },
  "pass_threshold": 0.75
}
```

Rubric weights across criteria must sum to `1.0` (tolerance ±0.05).

---

## Curation Workflow

### 1. Validate Existing Datasets

```bash
python scripts/curate_golden_dataset.py --validate-all
```

Checks JSON syntax, `golden_datasets/schema.json` conformance, duplicate IDs, rubric weight sums, nested PVS artifact schemas, secret scanning, and prints SHA-256 dataset checksums.

### 2. Add New Samples Interactively

```bash
python scripts/curate_golden_dataset.py --agent discovery --interactive
```

Or append a validated JSON sample non-interactively:

```bash
python scripts/curate_golden_dataset.py --agent discovery --add-file new_sample.json
```

### 3. Security & Privacy Rules

- **Synthetic data only**: Never copy real production customer records, names, emails, or credentials into golden datasets.
- **Zero secrets**: Run secret scanning (`SecretDetector` / `scripts/curate_golden_dataset.py --validate-all`) before committing.
- **Integrity**: Dataset SHA-256 checksums are logged on every evaluation run.

---

## Running Regression Evaluation

```bash
# Fast PR-mode evaluation (10-sample subset per agent)
pvs eval all --fast --subset-size 10 --threshold 0.75

# Full 180-sample suite across all 5 agents
pvs eval all --threshold 0.75 --baseline-dir .eval_baselines

# Single agent with JSON report export
pvs eval discovery --threshold 0.80 --output-json eval_results_discovery.json

# Filter by HELM category
pvs eval review --category adversarial --threshold 0.80
```

When neither `OPENAI_API_KEY` nor `ANTHROPIC_API_KEY` is set (or `--use-mock` is passed), `pvs eval` runs with deterministic sample-aware mock model adapters (`$0` API cost) while still exercising real agents, `Harness` prompt-injection guards, schema validation, and `RubricComplianceJudge`. Set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` to evaluate live LLMs with AI judges.

---

## Maintenance & Troubleshooting

- **Quarterly review**: Add samples for newly introduced agent capabilities and remove stale examples.
- **Version bumps**: Copy `dataset_v1.jsonl` to `dataset_v2.jsonl`, increment `"version": 2`, and note changes in `CHANGELOG.md`.
- **Unexpected pass-rate drop**: Compare `eval_results_<agent>.json` against `.eval_baselines/<agent>_v1_baseline.json` to isolate newly failing sample IDs and categories.
- **High evaluation cost**: Use `--fast --subset-size 10` on PRs and reserve the full 180-sample run for merges to `main` or nightly schedules.
