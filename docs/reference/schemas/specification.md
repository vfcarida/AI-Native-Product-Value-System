# Schema Reference: Product Specification

**Schema File**: [`schemas/specification.schema.json`](../../../schemas/specification.schema.json)  
**Example File**: [`schemas/examples/specification_example.json`](../../../schemas/examples/specification_example.json)

## Required Fields

- `id` (`string`, pattern `^spec-[a-z0-9-]+$`)
- `version` (`integer`, minimum `1`)
- `created` (`string`, ISO 8601 `date-time`)
- `outcome_id` (`string`, pattern `^outcome-[a-z0-9-]+$`)
- `functional_requirements` (`array`, non-empty items with `id` matching `^FR-[0-9]{3}$`, `description`, `acceptance_criteria`)
- `quality_attributes` (`object` with `performance`, `reliability`, `security`, `scalability`)

## Optional Fields

- `architecture_decisions` (`array` of ADR items with `id`, `decision`, `rationale`, `alternatives`, `trade_offs`)
- `testability_criteria` (`unit_tests`, `integration_tests`, `acceptance_tests`)
- `constraints` (`technical`, `operational`, `business`)
- `interfaces` (`REST`, `GraphQL`, `gRPC`, `Queue`, `Stream`)
