# Git History Analysis for Brownfield Modernization (EVO-030)

Git history analysis brings *"Your Code as a Crime Scene"* temporal intelligence into the Brownfield Agent, combining commit churn, temporal coupling, code age, and ownership/bus-factor metrics with static complexity analysis to prioritize refactoring where it matters most.

---

## How Temporal Analysis Works

```
Git Repository (read-only)
       │
       ▼
  GitAnalyzer  ── git log --no-merges --numstat (fast streaming, binary-filtered, anonymized)
       ├─▶ HotspotDetector    (commit frequency, lines changed, top-10% churn rank)
       ├─▶ CouplingAnalyzer   (file co-change matrix, coupling coefficient >= 0.5)
       ├─▶ AgeAnalyzer        (first/last commit dates, volatile vs. stable_legacy)
       └─▶ OwnershipAnalyzer  (anonymized author share, bus factor, knowledge silos)
       │
       ▼
  BrownfieldAgent  ── merges GitInsights × StaticComplexityAnalyzer (AST cyclomatic complexity)
       └─▶ ModernizationReport (ranked refactor priorities, coupling & silo risks, Markdown)
```

Core modules:
- [src/product_value_system/brownfield/git_analyzer.py](../src/product_value_system/brownfield/git_analyzer.py) — `GitAnalyzer`, `GitInsights`, `load_brownfield_config()`
- [src/product_value_system/brownfield/hotspot_detector.py](../src/product_value_system/brownfield/hotspot_detector.py) — `Hotspot`, `HotspotDetector`
- [src/product_value_system/brownfield/coupling_analyzer.py](../src/product_value_system/brownfield/coupling_analyzer.py) — `Coupling`, `CouplingAnalyzer`
- [src/product_value_system/brownfield/age_analyzer.py](../src/product_value_system/brownfield/age_analyzer.py) — `FileAge`, `AgeAnalyzer`
- [src/product_value_system/brownfield/ownership_analyzer.py](../src/product_value_system/brownfield/ownership_analyzer.py) — `Ownership`, `OwnershipAnalyzer`
- [src/product_value_system/agents/brownfield_agent.py](../src/product_value_system/agents/brownfield_agent.py) — `BrownfieldAgent`, `StaticComplexityAnalyzer`, `ModernizationReport`, `merge_insights()`
- [config/brownfield.yaml](../config/brownfield.yaml) — YAML configuration

---

## Interpreting Modernization Priorities

| Signal | What It Reveals | Recommended Action |
|---|---|---|
| **Hotspot × High Complexity** | High-churn file with dense branching logic (`priority_score = commits * complexity * silo_multiplier`) | **Refactor Priority #1**: add characterization tests and decompose responsibilities |
| **Temporal Coupling (`>= 0.5`)** | Two files consistently change in the same commits despite no obvious import link | Inspect hidden shared assumptions or extract a shared domain contract |
| **Knowledge Silo (`>= 80%` single author)** | Bus factor = `1`; high organizational risk if maintainer rotates | Pair-refactor, enforce review rotation, document invariants |
| **`stable_legacy` (untouched > 1 year)** | Battle-tested code with zero recent churn | **Leave alone** — avoid unnecessary modernization risk |

---

## Configuration (`config/brownfield.yaml`)

```yaml
brownfield:
  git_analysis:
    enabled: true
    lookback_days: 180
    min_commits_for_hotspot: 10
    coupling_threshold: 0.5
    min_co_changes: 2
    max_files_per_commit: 50
    silo_threshold_pct: 80.0
    anonymize_authors: true
    include_patterns:
      - "*.py"
      - "*.js"
      - "*.ts"
      - "*.java"
      - "*.go"
    exclude_patterns:
      - "node_modules/**"
      - "vendor/**"
      - "*.test.py"
      - "*.spec.js"
      - "test_*.py"
    max_commits_analyzed: 10000
```

### Privacy & Read-Only Guarantees

- **Read-Only**: Only read-only `git log` / `git rev-parse` commands are executed; `.git` and the working tree are never modified.
- **No Raw Author Emails**: With `anonymize_authors: true` (default), author emails are hashed deterministically to pseudonyms (`author_<sha256[:10]>`) so ownership percentages remain exact while zero PII appears in serialized reports or LLM prompts.

---

## Usage Example

```python
import asyncio
from pathlib import Path
from product_value_system.agents import BrownfieldAgent

async def main() -> None:
    agent = BrownfieldAgent(config="config/brownfield.yaml")
    report = await agent.analyze_repository(Path("."))
    print(report.to_markdown())

asyncio.run(main())
```

---

## Performance Tips for Large Repositories

1. **Streaming `--numstat`**: `GitAnalyzer` parses `git log --no-merges --numstat` in a single pass, processing 10,000+ commits in seconds while automatically skipping binary files (`-\t-\t`) and noisy merge commits.
2. **Bound Lookback & Commit Cap**: Tune `lookback_days` (default `180`) and `max_commits_analyzed` (default `10000`) for monorepos with 100K+ commits.
3. **Exclude Generated / Vendor Trees**: Keep `node_modules/**`, `vendor/**`, and build directories in `exclude_patterns` and cap `max_files_per_commit` (default `50`) so bulk formatter commits do not distort coupling matrices.
4. **In-Memory Caching**: Results are cached per `(repo_path, HEAD_sha)` for instant repeat queries.

---

## Rollback

To disable temporal git analysis and revert to static-only brownfield analysis:

```yaml
brownfield:
  git_analysis:
    enabled: false
```

---

## Related Documentation

- [Automated Drift Detection](drift-detection.md)
- [Architecture Decision Records (ADR)](adr-guide.md)
- [Architecture & Diagrams](ARCHITECTURE.md)
