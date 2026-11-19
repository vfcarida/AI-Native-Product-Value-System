# Schema Reference: Agent Definition

**Schema File**: [`schemas/agent_definition.schema.json`](../../../schemas/agent_definition.schema.json)  
**Example File**: [`schemas/examples/agent_definition_example.json`](../../../schemas/examples/agent_definition_example.json)

## Required Fields

- `apiVersion` (`"pvs.ai/v1alpha1"`)
- `kind` (`"AgentDefinition"`)
- `metadata` (`name`, `version`, optional `description`)
- `spec` (`objective`, `model`, `allowedTools`, `autonomy`)
