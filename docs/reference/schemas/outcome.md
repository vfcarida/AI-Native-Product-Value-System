# Schema Reference: Outcome Hypothesis

**Schema File**: [`schemas/outcome.schema.json`](../../../schemas/outcome.schema.json)  
**Example File**: [`schemas/examples/outcome_example.json`](../../../schemas/examples/outcome_example.json)

## Required Fields

- `id` (`string`, pattern `^outcome-[a-z0-9-]+$`)
- `version` (`integer`, minimum `1`)
- `created` (`string`, ISO 8601 `date-time`)
- `problem_id` (`string`, pattern `^problem-[a-z0-9-]+$`)
- `hypothesis` (`string`, length `50`–`3000`)
- `success_criteria` (`object`, required `primary_metric`, `baseline`, `target`)

## Validation

```python
from product_value_system.contracts.validation import SchemaValidator

validator = SchemaValidator("schemas")
validator.validate(outcome, "outcome.schema.json")
```
