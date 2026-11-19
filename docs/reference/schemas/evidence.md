# Schema Reference: Evidence Artifact

**Schema File**: [`schemas/evidence.schema.json`](../../../schemas/evidence.schema.json)  
**Example File**: [`schemas/examples/evidence_example.json`](../../../schemas/examples/evidence_example.json)

## Required Fields

- `id` (`string`, pattern `^(evidence|spec|build|release|run|outcome)-[a-z0-9-]+$`)
- `version` (`integer`, minimum `1`)
- `created` (`string`, ISO 8601 `date-time`)
- `type` (`test_result`, `review`, `architecture_review`, `security_review`, `evaluation`, `measurement`, `approval`)
- `verdict` (`pass`, `fail`, `warn`, `blocked`, `approve`, `approve_with_concerns`, `reject`)

## Optional Fields

- `subject_id` / `artifact_id`
- `score` (`0.0` to `1.0`)
- `rationale` / `summary`
- `findings` (structured findings with `severity`, `category`, `title`, `description`, `recommendation`)
- `artifacts` (supporting files with `name`, `path`, `mime_type`)
