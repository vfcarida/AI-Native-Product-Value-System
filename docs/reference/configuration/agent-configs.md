# Configuration Reference: Agent Configs

Agent configurations are YAML files in `config/agents/` validated against [`schemas/agent_definition.schema.json`](../../../schemas/agent_definition.schema.json).

## Format

```yaml
apiVersion: pvs.ai/v1alpha1
kind: AgentDefinition

metadata:
  name: <agent-name>
  version: <semver>
  description: <description>

spec:
  objective: <objective-description>

  model:
    provider: <openai|anthropic|google|azure|local>
    model: <model-id>
    temperature: <0.0-2.0>
    max_tokens: <int>

  allowedTools:
    - <tool-name>

  deniedTools:
    - <tool-name>

  autonomy:
    level: <0-4>
    maxIterations: <int>
    maxCostUsd: <float>
    humanApproval:
      requiredFor:
        - <action-type>

  outputs:
    - type: <artifact-type>
      schema: <schema-uri>
```

## Included Agent Configs

- [`config/agents/discovery_agent.yaml`](../../../config/agents/discovery_agent.yaml)
- [`config/agents/specification_agent.yaml`](../../../config/agents/specification_agent.yaml)
- [`config/agents/architecture_review_agent.yaml`](../../../config/agents/architecture_review_agent.yaml)
- [`config/agents/security_review_agent.yaml`](../../../config/agents/security_review_agent.yaml)
- [`config/agents/build_agent.yaml`](../../../config/agents/build_agent.yaml)
