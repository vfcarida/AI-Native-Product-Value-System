# Schema Reference: Problem Statement

**Schema File**: [`schemas/problem.schema.json`](../../../schemas/problem.schema.json)  
**Example File**: [`schemas/examples/problem_example.json`](../../../schemas/examples/problem_example.json)

## Required Fields

- `id` (`string`, pattern `^problem-[a-z0-9-]+$`)
- `version` (`integer`, minimum `1`)
- `created` (`string`, ISO 8601 `date-time`)
- `statement` (`string`, length `20`–`2000`)
- `context` (`object`, required `user_segment`, `business_context`)
- `constraints` (`object`, required `synthetic_only`)

## Validation

```python
from product_value_system.contracts.validation import SchemaValidator

validator = SchemaValidator("schemas")
validator.validate(problem, "problem.schema.json")
```
