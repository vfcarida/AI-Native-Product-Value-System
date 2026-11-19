# Brownfield Example: Legacy Customer API

## Overview

This example demonstrates brownfield system analysis using the AI-native Product Value System.

**Scenario**: AcmeBank has a legacy Customer API with outdated documentation and minimal tests.

## Problem

- Documentation last updated 15 months ago
- Test coverage: 23%
- 8 production incidents in 6 months due to undocumented behavior
- New developers take 3 days to integrate (target: 4 hours)

## Artifacts

- `legacy_api/src/CustomerService.java` — Legacy code with issues (read-only)
- `legacy_api/README.md` — Outdated documentation (v1.0, actual code v2.3)
- `problem_document_legacy.yaml` — Problem statement
- `expected_specification_from_code.md` — Expected specification (reference)
- `drift_report.json` — Structured code-vs-documentation drift analysis
- `golden_dataset/` — Golden dataset for evaluation

## Running the Example

### Execute Brownfield Analysis

```bash
# Run documentation bootstrap
pvs run examples/brownfield/problem_document_legacy.yaml

# Using Makefile
make demo EXAMPLE=brownfield/legacy_api
# Or directly:
make demo-brownfield
```

## No Code Modifications

This example **analyzes** legacy code but does **not** modify it. The legacy code in `legacy_api/src/CustomerService.java` remains unchanged, ensuring safety.
