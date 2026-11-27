# CLI Reference (`pvs`)

**Entry Point**: [`src/product_value_system/cli/main.py`](../../../src/product_value_system/cli/main.py) (Phase-2 & diagnostics in [`commands_p2.py`](../../../src/product_value_system/cli/commands_p2.py))

## Global Options

| Flag | Description |
|---|---|
| `--version` | Show `pvs` version and exit |
| `--config PATH` | Path to configuration file (default: `~/.pvs/config.yaml`) |
| `--no-color` | Disable colored output |
| `--help` | Show help message and exit |

## Semantic Exit Codes

| Exit Code | Meaning |
|---:|---|
| `0` | Success |
| `1` | General runtime or argument error |
| `6` | Diagnostic / health check failure (`pvs doctor`) |
| `130` | Interrupted by user (`Ctrl+C`) |

---

## Diagnostics, Control Plane, Governance, FinOps, Brownfield & DevX Subcommands

### `pvs doctor`
Validate Python runtime, all 16 strict `config/*.yaml` Pydantic contracts, Cedar `PolicyEngine`, JSON schemas, and LLM provider environment readiness.
```bash
pvs doctor [--repo-root .] [--json]
```

### `pvs serve`
Run the production FastAPI/Uvicorn ASGI Control Plane exposing `/api/v1/runs`, `/api/v1/mcp`, `/.well-known/agent.json`, `/.well-known/oauth-protected-resource`, `/api/v1/a2a/*`, signed GitHub/Slack webhooks, `/health/*`, `/metrics`, and `/devx`.
```bash
pvs serve [--host 0.0.0.0] [--port 8000] [--workers 1] [--reload]
```

### `pvs governance export`
Generate an auditor-ready EU AI Act (Annex IV / Article 14) + NIST AI RMF 1.0 Governance Audit Pack in JSON or Markdown, optionally running the live OWASP ASI01–ASI10 red-teaming gate.
```bash
pvs governance export [--format json] [--repo-root .] [--include-redteam] [--output governance-pack.json]
```

### `pvs route`
Classify prompt complexity (EVO-026) and inspect selected model tier, estimated cost per 1K tokens, and fallback chain.
```bash
pvs route --prompt "Convert CSV to JSON" [--task-type format_conversion] [--config config/routing.yaml] [--json]
```

### `pvs brownfield`
Run read-only Git history hotspot/coupling analysis (EVO-030) and optional Strangler Fig module boundary clustering (EVO-031).
```bash
pvs brownfield --repo . [--days 90] [--strangler] [--json]
```

### `pvs devx`
Inspect privacy-preserving SPACE & DORA telemetry configuration and current aggregate snapshot (EVO-032).
```bash
pvs devx [--config config/metrics.yaml] [--json]
```

---

## Core Value Stream Subcommands

### `pvs init`
Initialize a new problem statement template.
```bash
pvs init [--output problem.yaml]
```

### `pvs run`
Execute a complete value stream run from a problem statement YAML file.
```bash
pvs run <problem.yaml> [--config-override KEY=VALUE]
```

### `pvs status` / `pvs list` / `pvs show`
Inspect persisted value stream runs, stage cost breakdown, and artifact paths.
```bash
pvs list [--limit 10]
pvs status <run_id>
pvs show <run_id>
```

### `pvs validate`
Validate an artifact against its JSON Schema 2020-12 contract.
```bash
pvs validate <artifact_path> --type <problem|outcome|specification|evidence>
```

---

## Extended Domain Subcommands (EVO-009..EVO-023)

| Command | Domain | Verified Invocation |
|---|---|---|
| `pvs run-simulation` | Synthetic customer simulation (EVO-009) | `pvs run-simulation examples/simulation/focus_group.yaml` |
| `pvs measure-outcome` | Post-launch outcome measurement (EVO-010) | `pvs measure-outcome --outcome-id out-1 --use-mock-analytics` |
| `pvs eval` | Golden dataset regression evaluation (EVO-011) | `pvs eval discovery --dataset-dir golden_datasets --use-mock` |
| `pvs review` | Multi-agent specification review (EVO-013) | `pvs review <spec.yaml> --use-mock` |
| `pvs flags` | Feature flag & rollout management (EVO-015) | `pvs flags list` |
| `pvs experiment` | A/B power analysis & experiment design (EVO-016) | `pvs experiment sample-size --baseline 0.1 --mde 0.02` |
| `pvs drift` | Spec-to-code drift detection (EVO-017) | `pvs drift check` |
| `pvs adr` | Architecture Decision Record management (EVO-018) | `pvs adr list` |
| `pvs spec` | Spec-Kit templates & traceability (EVO-020) | `pvs spec validate <spec.md>` |
| `pvs interview` | AI customer interview synthesis (EVO-022) | `pvs interview synthesize <transcript.vtt>` |
| `pvs evidence` | Multi-source evidence synthesis (EVO-023) | `pvs evidence synthesize checkout --tickets <tickets.json>` |
