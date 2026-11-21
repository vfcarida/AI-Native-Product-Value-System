# Customer Interview Synthesis Guide (EVO-022)

The Product Value System automates customer discovery synthesis from raw interview transcripts into structured, privacy-safe, traceable product insights.

---

## Pipeline Overview

1. **Transcript Processing (`TranscriptProcessor`)**: Parses WebVTT (`.vtt`), SubRip (`.srt`), and plain text transcripts with speaker labels, timestamps (`HH:MM:SS`), turn numbers, and line numbers.
2. **Multi-Layer PII Redaction (`PIIRedactor`)**: Redacts emails, Brazilian & international phone numbers, CPFs, credit cards, street addresses (English & Portuguese), and person names **before** LLM prompting or persistence.
3. **Insight Extraction (`InsightExtractor`)**: Extracts pain points (with `critical`/`high`/`medium`/`low` severity & `0–1` confidence), Jobs-to-be-Done (JTBD), verbatim quotes with speaker/timestamp/turn attribution, themes, executive summary, and human review workflow state (`pending_review`, `approved`, `rejected`, `needs_revision`).
4. **Cross-Interview Theme Clustering (`ThemeClusterer`)**: Groups semantically related pain points across interviews using cosine-distance DBSCAN (`similarity_threshold` tunable). Supports custom embedding functions as well as a deterministic offline semantic + TF-IDF embedding model.
5. **Opportunity Map & Exports (`OpportunityMap`, `exporters`, `SynthesisDashboard`)**: Populates prioritized opportunities for Continuous Discovery (EVO-005), exports JSON/Markdown/Notion formats, and aggregates insights over time and trending themes.

---

## Example Interview Transcript

```text
[00:01:30] Interviewer: What's your biggest frustration with transfers?
[00:01:35] Participant: It takes forever! Sometimes 10-15 seconds just waiting.
[00:02:10] Interviewer: How often do you make transfers?
[00:02:15] Participant: Daily, sometimes 5-10 times per day.
```

---

## Python API Usage

### Single Interview & Batch Synthesis

```python
from pathlib import Path
from product_value_system.discovery import InterviewSynthesizer, OpportunityMap

synthesizer = InterviewSynthesizer(llm=my_async_llm_callable)  # or llm=None for offline mode

# Synthesize one interview (VTT, SRT, or plain text)
insights = await synthesizer.synthesize(Path("tests/fixtures/sample-interview.txt"))

# Human review workflow
insights.approve(notes="Validated quotes and severity ratings")

# Batch synthesize a directory of transcripts
all_insights = await synthesizer.synthesize_batch(
    Path("transcripts/"),
    output_dir=Path("analysis/"),
    export_format="json",
)

# Cluster themes across interviews and populate Opportunity Map (EVO-005)
themes = await synthesizer.cluster_themes(all_insights, similarity_threshold=0.7)
opportunity_map = OpportunityMap(desired_outcome="Reduce PIX transfer friction")
opportunity_map.populate_from_themes(themes)
```

### PII Redaction

```python
from product_value_system.discovery import PIIRedactor

redactor = PIIRedactor()
text = "Call João Silva at joao@example.com or +55 (11) 98765-4321 (123 Rua Example)"
redacted, counts = redactor.redact(text)
# -> "Call [NAME] at [EMAIL] or [PHONE] ([ADDRESS])"
```

---

## CLI Reference

```bash
# Synthesize a single interview transcript (VTT, SRT, or plain text)
pvs interview synthesize tests/fixtures/sample-interview.txt --output analysis/INT-001.json

# Batch-synthesize all transcripts in a directory
pvs interview synthesize-batch transcripts/ --output analysis/ --format json

# Cluster semantically similar pain points across interviews and populate Opportunity Map
pvs interview cluster-themes analysis/*.json --threshold 0.7 --opportunity-map analysis/opportunities.json

# Export a synthesis JSON file to Markdown or Notion-compatible format
pvs interview export analysis/INT-001.json --format notion --output analysis/INT-001-notion.md

# View insights-over-time and trending themes dashboard
pvs interview dashboard analysis/ --format markdown
```

---

## Installation

```bash
pip install -e ".[discovery]"
```

See also: [README](../README.md), [Spec-Kit Patterns](spec-kit-guide.md), [OpenTelemetry GenAI Conventions](otel-genai-conventions.md).
