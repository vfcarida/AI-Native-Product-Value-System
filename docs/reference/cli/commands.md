# CLI Reference

**Entry Point**: [`src/product_value_system/cli/main.py`](../../../src/product_value_system/cli/main.py)

## Global Options

- `--version` — Show `pvs` version and exit
- `--config PATH` — Path to configuration file (default: `~/.pvs/config.yaml`)
- `--no-color` — Disable colored output
- `--help` — Show help message and exit

---

## Subcommands

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

### `pvs status`

Display current status and stage summary for a value stream run.

```bash
pvs status <run_id>
```

### `pvs list`

List recent value stream runs from the SQLite persistence database.

```bash
pvs list [--limit 10]
```

### `pvs show`

Display full run details, cost breakdown by agent, and stage artifact paths.

```bash
pvs show <run_id>
```

### `pvs validate`

Validate an artifact against its JSON Schema 2020-12 contract.

```bash
pvs validate <artifact_path> --type <problem|outcome|specification|evidence>
```
