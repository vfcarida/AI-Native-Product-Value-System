# Multi-Source Evidence Synthesis Guide (EVO-023)

The Product Value System triangulates product evidence across qualitative and quantitative sources—weighting every signal by recency, sample size, and source quality while surfacing contradictions before high-stakes product bets.

---

## Architecture

1. **Evidence Collection (`EvidenceCollector`)**: Normalizes inputs from EVO-022 interview syntheses (JSON or in-memory `InterviewInsights`), support ticket exports (JSON & CSV), product analytics reports, competitor research Markdown docs, and external URLs.
2. **Evidence Quality Scoring (`QualityScorer`)**: Computes a `0–1` weight for each source using:
   - **Recency decay** (`30%` weight): exponential decay with ~125-day half-life (`~0.37` at 180 days).
   - **Sample size** (`40%` weight): logarithmic scale (`log10(n + 1) / 3`, capping at `1.0` for `n >= 1000`, `0.5` when unknown).
   - **Source quality hierarchy** (`30%` weight): `PRIMARY = 1.0`, `SECONDARY = 0.6`, `TERTIARY = 0.3`.
   - Multiplied by the item's intrinsic `confidence`.
3. **Contradiction Detection (`ContradictionDetector`)**: Pairs semantically similar claims (`cosine similarity >= threshold`) and evaluates semantic, quantitative, and temporal conflicts (LLM-backed or deterministic offline heuristic). High-severity contradictions (`>= 0.7`) are flagged `requires_human_review = True`.
4. **Synthesis Engine (`SynthesisEngine`)**: Groups related evidence into focused `Claim`s, lets the higher-quality-weighted side lead while preserving contradicting evidence explicitly, calculates `agreement_score` (`0–1`) and `overall_confidence` (`0–1`), builds source provenance, detects research gaps, and outputs prioritized recommendations.
5. **Reports & Network Visualization (`reporter`)**: Exports JSON, Markdown (with embedded Mermaid `flowchart LR`), and standalone interactive HTML/SVG claim-to-source network graphs.

---

## Python API Example (Biometric Transfer Triangulation)

```python
from product_value_system.evidence import EvidenceSynthesisAgent

agent = EvidenceSynthesisAgent()  # Works offline or with llm_callable & embedding_model
report = await agent.synthesize_sources(
    topic="Biometric Transfer Feature",
    interview_files=["tests/fixtures/interviews/interview-001-synthesis.json"],
    ticket_file="tests/fixtures/evidence/support-tickets.json",
    analytics="tests/fixtures/evidence/analytics-report.json",
    research_docs=["tests/fixtures/evidence/competitor-research.md"],
)

agent.save_report(report, "synthesis/biometric-transfer.md")
agent.save_report(report, "synthesis/biometric-transfer.json")
agent.save_network_html(report, "synthesis/evidence-network.html")
```

---

## CLI Reference

```bash
# Synthesize evidence across interviews, support tickets, analytics, and competitor research
pvs evidence synthesize "Biometric Transfer" \
  --interviews tests/fixtures/interviews/*.json \
  --tickets tests/fixtures/evidence/support-tickets.json \
  --analytics tests/fixtures/evidence/analytics-report.json \
  --research tests/fixtures/evidence/competitor-research.md \
  --output synthesis/biometric-transfer.md \
  --network-html synthesis/evidence-network.html

# Save JSON synthesis and render standalone HTML or Mermaid network visualization
pvs evidence synthesize "Biometric Transfer" \
  --interviews tests/fixtures/interviews/*.json \
  --analytics tests/fixtures/evidence/analytics-report.json \
  --output synthesis/biometric-transfer.json

pvs evidence visualize synthesis/biometric-transfer.json --output synthesis/evidence-network.html
pvs evidence visualize synthesis/biometric-transfer.json --format mermaid
```

---

## Installation

```bash
pip install -e ".[evidence]"
```

See also: [README](../README.md), [Customer Interview Synthesis](interview-synthesis.md), [Baseline Validation](baseline-validation.md).
