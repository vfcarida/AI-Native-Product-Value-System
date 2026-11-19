# Greenfield Example: Payment Notification Service

## Overview

This example demonstrates a complete greenfield product development flow using the AI-native Product Value System.

**Scenario**: AcmeBank wants to add real-time payment notifications to their mobile app.

## Artifacts

- `problem_payment_flow.yaml` — Problem statement with synthetic customer research
- `expected_outcome.yaml` — Expected outcome hypothesis with success metrics
- `expected_specification.md` — Expected specification (reference implementation)
- `reference_implementation/` — Reference Python implementation and pytest suite
- `golden_dataset/` — Golden dataset for evaluation (5 samples)

## Running the Example

### Prerequisites

```bash
# Install Product Value System
pip install -e .

# Verify installation
pvs --version
```

### Execute Value Stream Run

```bash
# Run complete value stream
pvs run examples/greenfield/problem_payment_flow.yaml

# Check status
pvs status <run-id>

# View results
pvs show <run-id>
```

### Using Makefile

```bash
# Run greenfield demo
make demo EXAMPLE=greenfield/payment_flow
# Or directly:
make demo-greenfield
```

## Expected Outputs

After successful execution, the system will generate:

1. **Outcome Hypothesis** (`artifacts/runs/<problem-id>/outcome.yaml`)
   - Success metrics defined
   - Evidence requirements specified
   - Risks identified

2. **Specification** (`artifacts/runs/<problem-id>/specification.yaml`)
   - Functional requirements
   - Architecture design
   - API specifications
   - Testing requirements

3. **Architecture Review** (`artifacts/runs/<problem-id>/architecture_review.yaml`)
   - Technical soundness assessment
   - Risk analysis
   - Recommendations

4. **Security Review** (`artifacts/runs/<problem-id>/security_review.yaml`)
   - Security assessment
   - Compliance check
   - Remediation recommendations

## Synthetic Data

All customer data, metrics, and business context in this example are **synthetic** and created for demonstration purposes only. No real customer information is used.
