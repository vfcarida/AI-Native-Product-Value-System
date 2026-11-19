# Architecture Diagrams

This document contains all architecture diagrams for the AI-native Product Value System.

**Diagram Format**: Mermaid (text-based, renders natively in GitHub)

---

## 1. System Context (C4 Level 1)

**Source**: [01-system-context.mmd](diagrams/01-system-context.mmd)

```mermaid
graph TB
    subgraph "AI-Native Product Value System"
        PVS["Product Value System - Orchestrates AI agents through value stream"]
    end

    User["User - Product Manager, Engineer"]
    ModelAPI["Model API - OpenAI, Anthropic, Google"]
    GitRepo["Git Repository - Artifact storage"]
    ObsBackend["Observability Backend - Jaeger, Prometheus"]

    User -->|problem.yaml| PVS
    PVS -->|LLM calls| ModelAPI
    PVS -->|commits artifacts| GitRepo
    PVS -->|traces, metrics| ObsBackend
    PVS -->|outcome.yaml| User

    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef external fill:#999999,stroke:#6b6b6b,color:#ffffff

    class PVS system
    class User,ModelAPI,GitRepo,ObsBackend external
```

**Alt Text:** System context diagram showing the Product Value System at the center, with interactions to User (input/output), Model APIs (LLM calls), Git Repository (artifact storage), and Observability Backend (telemetry).

---

## 2. Container Diagram (C4 Level 2)

**Source**: [02-container-diagram.mmd](diagrams/02-container-diagram.mmd)

```mermaid
graph TB
    User["User - CLI or API"]

    subgraph "Product Value System"
        CLI["CLI - Python argparse"]
        Orchestrator["Orchestrator - State machine"]
        AgentRegistry["Agent Registry - Agent management"]
        PolicyEngine["Policy Engine - Cedar authorization"]
        Sandbox["Sandbox Provider - Docker containers"]
        Persistence["Persistence Layer - SQLite database"]
        Observability["Observability - OpenTelemetry"]

        CLI --> Orchestrator
        Orchestrator --> AgentRegistry
        Orchestrator --> Persistence
        Orchestrator --> Observability
        AgentRegistry --> PolicyEngine
        AgentRegistry --> Sandbox
    end

    ModelAPI["Model APIs - OpenAI, Anthropic, Google"]
    GitRepo["Git Repository"]
    ObsBackend["Observability Backend"]

    User --> CLI
    AgentRegistry --> ModelAPI
    Orchestrator --> GitRepo
    Observability --> ObsBackend

    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef external fill:#999999,stroke:#6b6b6b,color:#ffffff

    class CLI,Orchestrator,AgentRegistry,PolicyEngine,Sandbox,Persistence,Observability container
    class User,ModelAPI,GitRepo,ObsBackend external
```

**Alt Text:** Container diagram showing Product Value System internal containers: CLI, Orchestrator, Agent Registry, Policy Engine, Sandbox Provider, Persistence Layer, and Observability. External systems include User, Model APIs, Git Repository, and Observability Backend.

---

## 3. Component Diagram: Harness System (C4 Level 3)

**Source**: [03-component-diagram.mmd](diagrams/03-component-diagram.mmd)

```mermaid
graph TB
    subgraph "Harness System"
        Harness["Harness Controller"]

        subgraph "Components"
            SystemPrompt["System Prompt - Instructions and Examples"]
            Guardrails["Guardrails - Limits and Safety"]
            Memory["Memory - Short-term and Long-term"]
            ToolGuidance["Tool Registry - Policy-gated execution"]
            Budget["Budget - Cost, Time, Iterations"]
        end

        Harness --> SystemPrompt
        Harness --> Guardrails
        Harness --> Memory
        Harness --> ToolGuidance
        Harness --> Budget
    end

    Agent["Agent"]
    PolicyEngine["Policy Engine"]
    ModelAdapter["Model Adapter"]

    Agent --> Harness
    Harness --> PolicyEngine
    Harness --> ModelAdapter

    classDef component fill:#3498db,stroke:#2980b9,color:#ffffff
    classDef external fill:#95a5a6,stroke:#7f8c8d,color:#ffffff

    class SystemPrompt,Guardrails,Memory,ToolGuidance,Budget,Harness component
    class Agent,PolicyEngine,ModelAdapter external
```

**Alt Text:** Component diagram showing Harness System components: System Prompt, Guardrails, Memory, Tool Registry, and Budget. Harness Controller coordinates components and connects to Agent, Policy Engine, and Model Adapter.

---

## 4. Value Stream Run Sequence

**Source**: [04-value-stream-sequence.mmd](diagrams/04-value-stream-sequence.mmd)

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant Orchestrator
    participant DiscoveryAgent
    participant SpecAgent
    participant BuildAgent
    participant Sandbox
    participant PolicyEngine
    participant ModelAPI
    participant GitRepo

    User->>CLI: run problem.yaml
    CLI->>Orchestrator: execute_run(problem)

    Note over Orchestrator: Stage: Discovery
    Orchestrator->>DiscoveryAgent: execute(problem, harness)
    DiscoveryAgent->>PolicyEngine: check_permission("knowledge.query")
    PolicyEngine-->>DiscoveryAgent: ALLOW
    DiscoveryAgent->>ModelAPI: invoke(messages)
    ModelAPI-->>DiscoveryAgent: response
    DiscoveryAgent->>GitRepo: commit(outcome.yaml)
    DiscoveryAgent-->>Orchestrator: outcome

    Note over Orchestrator: Stage: Specification
    Orchestrator->>SpecAgent: execute(outcome, harness)
    SpecAgent->>ModelAPI: invoke(messages)
    ModelAPI-->>SpecAgent: response
    SpecAgent->>GitRepo: commit(specification.yaml)
    SpecAgent-->>Orchestrator: specification

    Note over Orchestrator: Stage: Build
    Orchestrator->>BuildAgent: execute(spec, harness)
    BuildAgent->>Sandbox: create_sandbox()
    Sandbox-->>BuildAgent: sandbox_id
    BuildAgent->>Sandbox: execute_code(code)
    Sandbox-->>BuildAgent: result
    BuildAgent->>GitRepo: commit(implementation/)
    BuildAgent-->>Orchestrator: build_result

    Orchestrator-->>CLI: run_result
    CLI-->>User: success + artifacts
```

**Alt Text:** Sequence diagram showing a complete value stream run: User submits problem via CLI, Orchestrator coordinates Discovery Agent (with policy check and model call), Specification Agent, and Build Agent (with sandbox execution), committing artifacts to Git at each stage.

---

## 5. Policy Evaluation Sequence

**Source**: [05-policy-evaluation-sequence.mmd](diagrams/05-policy-evaluation-sequence.mmd)

```mermaid
sequenceDiagram
    participant Agent
    participant Harness
    participant PolicyEngine
    participant CedarEngine
    participant AuditLog

    Agent->>Harness: call_tool("knowledge.query", args)
    Harness->>PolicyEngine: evaluate(agent, action, resource, context)

    Note over PolicyEngine: Load policies
    PolicyEngine->>CedarEngine: authorize(principal, action, resource)

    alt Policy permits
        CedarEngine-->>PolicyEngine: ALLOW + policy_id
        PolicyEngine->>AuditLog: log(agent, action, ALLOW, policy_id)
        PolicyEngine-->>Harness: ALLOW
        Harness->>Agent: execute_tool(args)
        Agent-->>Harness: result
    else Policy denies
        CedarEngine-->>PolicyEngine: DENY + reason
        PolicyEngine->>AuditLog: log(agent, action, DENY, reason)
        PolicyEngine-->>Harness: DENY
        Harness-->>Agent: PermissionDenied(reason)
    end
```

**Alt Text:** Sequence diagram showing policy evaluation flow: Agent requests tool call via Harness, Harness checks with Policy Engine, Policy Engine evaluates using Cedar Engine, result logged to Audit Log, and permission granted or denied to Agent with rationale.

---

## 6. Trust Boundaries

**Source**: [06-trust-boundaries.mmd](diagrams/06-trust-boundaries.mmd)

```mermaid
graph TB
    subgraph "TRUSTED ZONE"
        UserInput["User Input - Schema-validated"]
        Policies["Cedar Policies - Schema-validated"]
        Schemas["JSON Schemas - Validated"]
        SignedImages["Container Images - Signed"]
    end

    subgraph "VALIDATION LAYER"
        InputVal["Input Validation"]
        OutputVal["Output Validation"]
        SchemaVal["Schema Validation"]
        PolicyVal["Policy Validation"]
    end

    subgraph "UNTRUSTED ZONE"
        LLMOutput["LLM Outputs - Treat as adversarial"]
        AgentCode["Agent-Generated Code - Execute in sandbox"]
        ExternalAPI["External API Responses - Validate schemas"]
    end

    UserInput --> InputVal
    Policies --> PolicyVal

    InputVal --> Orchestrator["Orchestrator"]
    PolicyVal --> Orchestrator

    Orchestrator --> OutputVal
    OutputVal --> LLMOutput

    LLMOutput --> SchemaVal
    SchemaVal --> AgentCode

    AgentCode --> Sandbox["Sandbox - Isolated Execution"]

    classDef trusted fill:#2ecc71,stroke:#27ae60,color:#ffffff
    classDef validation fill:#f39c12,stroke:#e67e22,color:#ffffff
    classDef untrusted fill:#e74c3c,stroke:#c0392b,color:#ffffff
    classDef neutral fill:#3498db,stroke:#2980b9,color:#ffffff

    class UserInput,Policies,Schemas,SignedImages trusted
    class InputVal,OutputVal,SchemaVal,PolicyVal validation
    class LLMOutput,AgentCode,ExternalAPI untrusted
    class Orchestrator,Sandbox neutral
```

**Alt Text:** Trust boundary diagram showing three zones: Trusted Zone (user input, policies, schemas), Validation Layer (input/output/schema/policy validation), and Untrusted Zone (LLM outputs, agent-generated code, external APIs). Arrows show data flowing through validation layers before reaching orchestrator and sandbox.

---

## 7. Data Flow: Artifact Lifecycle

**Source**: [07-data-flow.mmd](diagrams/07-data-flow.mmd)

```mermaid
graph LR
    A["problem.yaml"] -->|Discovery Agent| B["outcome.yaml"]
    B -->|Specification Agent| C["specification.yaml"]
    C -->|Build Agent| D["implementation/ - code + tests"]
    D -->|Review Agents| E["review/ - architecture + security"]
    E -->|Evaluation Pipeline| F["evaluation/ - scores + evidence"]
    F -->|Release Agent| G["release/ - decision + artifacts"]

    subgraph "Git Repository"
        A
        B
        C
        D
        E
        F
        G
    end

    H["SQLite Database"] -.->|Metadata| A
    H -.->|Costs| B
    H -.->|Policy Decisions| C

    classDef artifact fill:#3498db,stroke:#2980b9,color:#ffffff
    classDef storage fill:#95a5a6,stroke:#7f8c8d,color:#ffffff

    class A,B,C,D,E,F,G artifact
    class H storage
```

**Alt Text:** Data flow diagram showing artifact lifecycle from problem.yaml through outcome, specification, implementation, review, evaluation, to release decision. All artifacts stored in Git Repository. Database stores metadata, costs, and policy decisions alongside artifacts.

---

## 8. Deployment: MVP Single-Machine

**Source**: [08-deployment.mmd](diagrams/08-deployment.mmd)

```mermaid
graph TB
    subgraph "Developer Machine"
        subgraph "Python Process"
            Orchestrator["Orchestrator Process"]
            AgentRegistry["Agent Registry"]
            PolicyEngine["Policy Engine"]
        end

        subgraph "Docker Engine"
            Container1["Sandbox Container 1"]
            Container2["Sandbox Container N"]
        end

        Database[("SQLite Database")]
        GitRepo[("Git Repository")]

        Orchestrator --> AgentRegistry
        Orchestrator --> PolicyEngine
        Orchestrator --> Database
        Orchestrator --> GitRepo
        AgentRegistry --> Container1
        AgentRegistry --> Container2
    end

    Internet["Internet"]
    ModelAPIs["Model APIs - OpenAI, Anthropic"]

    Orchestrator -->|HTTPS| Internet
    Internet --> ModelAPIs

    classDef process fill:#3498db,stroke:#2980b9,color:#ffffff
    classDef storage fill:#95a5a6,stroke:#7f8c8d,color:#ffffff
    classDef container fill:#e67e22,stroke:#d35400,color:#ffffff
    classDef external fill:#999999,stroke:#6b6b6b,color:#ffffff

    class Orchestrator,AgentRegistry,PolicyEngine process
    class Database,GitRepo storage
    class Container1,Container2 container
    class Internet,ModelAPIs external
```

**Alt Text:** Deployment diagram showing single-machine MVP architecture. Developer machine runs Python processes (Orchestrator, Agent Registry, Policy Engine), Docker Engine with multiple sandbox containers, SQLite database, and Git repository. Orchestrator connects to external Model APIs via HTTPS through internet.

---

**Last Updated**: 2026-11-18
