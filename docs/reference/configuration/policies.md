# Configuration Reference: Cedar Policies

Declarative authorization policies are located in [`policies/agent_authorization.cedar`](../../../policies/agent_authorization.cedar) and validated against [`policies/schema.cedarschema`](../../../policies/schema.cedarschema).

## Entity Types

- `ProductValueSystem::AgentRole` (`name`, `trustLevel`)
- `ProductValueSystem::Agent` (`role`, `version`, `costBudget`, `allowedEnvironments`)
- `ProductValueSystem::Tool` (`classification`, `riskLevel`, `dataAccess`, `requiresApproval`)
- `ProductValueSystem::Action` (`read`, `write`, `execute`, `deploy`, `query`, `call`, `approve`)
- `ProductValueSystem::Environment` (`name`, `classification`)

## Context Attributes

- `environment`: `"sandbox" | "staging" | "production"`
- `has_human_approval`: `Bool`
- `cost_so_far`: `Decimal`
- `iteration_count`: `Long`
- `stage`: `String`

## Validation Command

```bash
make validate-policies
```
