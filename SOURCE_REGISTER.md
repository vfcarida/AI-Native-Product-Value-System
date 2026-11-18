# Source Register: Evidence and References

## 1. Purpose

This document catalogs all authoritative sources used in planning the AI-native Product Value System reference implementation. It establishes provenance, credibility, and appropriate interpretation of evidence.

## 2. Primary Source Documents

### 2.1 SOURCE-01: Strategic Playbook

**Title**: Playbook estratégico integrado para um PDLC, SDLC e Ways of Working AI-native em grandes empresas reguladas

**Location**: `/Users/vfcnbtk/Downloads/deep-research-report (3).md`

**Size**: 50,137 bytes (50KB)

**Language**: Portuguese

**Content Summary**:
- Defines AI-native Product Value System concept
- Establishes 7-layer taxonomy
- Synthesizes evidence from DORA, Anthropic, OpenAI, Thoughtworks, SVPG
- Addresses regulated enterprise context (Brazilian financial institutions)
- Integrates PDLC + SDLC + Ways of Working + Governance

**Key Contributions**:
- Value Stream Run as fundamental unit
- Evidence-based transformation approach
- Critique of "AI makes developers X% more productive" simplification
- Emphasis on context quality, governance, measurement
- Brownfield vs greenfield strategies
- Regulatory compliance patterns

**Assessment**: Authoritative synthesis of peer-reviewed research, industry reports, and official regulations. Citations verified. Portuguese language but technical content universally applicable.

### 2.2 SOURCE-02: Implementation Blueprint

**Title**: Implementações de referência, projetos open source e blueprint executável no GitHub

**Location**: `/Users/vfcnbtk/Downloads/deep-research-report (4).md`

**Size**: 37,241 bytes (37KB)

**Language**: Portuguese

**Content Summary**:
- Proposes reference architecture for GitHub implementation
- Analyzes open-source projects (LangGraph, OpenAI Agents SDK, Google ADK, A2A, E2B, OPA, etc.)
- Defines taxonomy: model ≠ agent ≠ harness ≠ orchestrator ≠ runtime
- Specifies technology stack recommendations
- Establishes MVP scope and milestones

**Key Contributions**:
- Value Stream Run as repository organizing principle
- Contract-first architecture (JSON Schema)
- Adapter pattern for vendor neutrality
- Threat model requirements
- Governance and licensing guidance
- Repository structure proposal

**Assessment**: Technical architecture document building on SOURCE-01 foundation. Incorporates research on contemporary agent frameworks and security standards.

### 2.3 SOURCE-03: Harness² Clarification

**Title**: Correção pontual — Harness² no AI-native Product Value System

**Location**: `/Users/vfcnbtk/Downloads/deep-research-report (5).md`

**Size**: 13,100 bytes (13KB)

**Language**: Portuguese

**Content Summary**:
- Corrects reference confusion about google-research/harness2
- Clarifies roles of Harness², Elephant-Goldfish Model, and Scion
- Explains recursive agent harnessing concept
- Details harness components (8-element model)
- Proposes integration into reference implementation

**Key Contributions**:
- Harness as first-class architectural layer
- Separation of concerns: harness (context/tools) vs orchestrator (coordination) vs quality (review)
- Evidence-based harness evolution (Propose-Probe-Compose)
- Harness Development Life Cycle (HDLC) concept

**Assessment**: Technical clarification document. Corrects project identification and provides deeper analysis of harness engineering patterns.

### 2.4 SOURCE-04: Project Brief

**Status**: **MISSING**

The user confirmed no project brief (SOURCE-04) is available. All scope assumptions derive from SOURCE-01, SOURCE-02, SOURCE-03 and must be validated by user before execution.

## 3. Research Sources (from Engineering Research Agent)

### 3.1 Official Documentation

| Source | URL | Usage | Status |
|--------|-----|-------|--------|
| PyPA pytest | https://docs.pytest.org/en/stable/ | Python testing practices | Stable, current |
| Setuptools | https://setuptools.pypa.io/ | Python packaging | Stable, current |
| OWASP LLM Top 10 | https://genai.owasp.org/llm-top-10/ | Security threats | 2025 edition |
| OpenTelemetry Docs | https://opentelemetry.io/docs/ | Observability patterns | v1.45.0 |
| OpenTelemetry GenAI | https://github.com/open-telemetry/semantic-conventions-genai | GenAI conventions | Emerging standard |
| Docker Security | https://docs.docker.com/engine/security/ | Container security | Current |
| GitHub Actions Security | https://docs.github.com/en/actions/security-for-github-actions | CI/CD security | Current |
| JSON Schema | https://json-schema.org/specification | Schema standard | 2020-12 (stable) |
| Temporal Documentation | https://docs.temporal.io/ | Durable workflows | Current |
| Open Policy Agent | https://www.openpolicyagent.org/docs/latest/ | Policy engine | CNCF Graduated |
| AWS Cedar | https://www.cedarpolicy.com/ | Authorization language | v4.6 |

### 3.2 Open Source Projects

| Project | Repository | License | Status | Usage |
|---------|-----------|---------|--------|-------|
| Harness² | https://github.com/google-research/harness2 | Apache 2.0 | v0.1.0 (experimental) | Harness patterns |
| Elephant-Goldfish | https://github.com/google/elephant-goldfish-model | Apache 2.0 | Active | Spec review patterns |
| Scion | https://github.com/GoogleCloudPlatform/scion | Apache 2.0 | Pre-1.0 | Orchestration patterns |
| LangGraph | https://github.com/langchain-ai/langgraph | MIT | Active | Durable execution reference |
| OpenAI Agents SDK | https://github.com/openai/openai-agents-python | Open source | Active | Agent runtime reference |
| Google ADK | https://github.com/google/adk-python | Apache 2.0 | Active | Agent runtime reference |
| A2A | https://github.com/a2aproject/A2A | Apache 2.0 | Linux Foundation | Interoperability protocol |
| E2B | https://github.com/e2b-dev/E2B | Apache 2.0 | Active | Sandbox reference |
| LiteLLM | https://github.com/BerriAI/litellm | MIT (core) | Active | Gateway reference (check license) |
| OPA | https://github.com/open-policy-agent/opa | Apache 2.0 | CNCF Graduated | Policy engine |
| Cedar | https://github.com/cedar-policy/cedar | Apache 2.0 | Active | Authorization engine |
| SPIRE | https://github.com/spiffe/spire | Apache 2.0 | CNCF Graduated | Workload identity |
| Promptfoo | https://github.com/promptfoo/promptfoo | MIT | Active | Eval framework |
| OpenAI Evals | https://github.com/openai/evals | MIT | Available | Eval methodology |
| Temporal SDK | https://github.com/temporalio/sdk-python | MIT | Active | Durable workflow SDK |
| CodeQL | https://github.com/github/codeql | MIT (queries) | Mature | Static analysis |

### 3.3 Referenced Research

| Topic | Source | Credibility | Key Finding |
|-------|--------|-------------|-------------|
| Developer Productivity | METR RCT (2025) | Peer-reviewed experiment | 19% increase in time with AI (nuanced results) |
| Developer Productivity | Microsoft/Accenture RCT | Peer-reviewed experiment | 26% increase in tasks completed |
| DORA Metrics | DORA Research Program | Longitudinal, cross-organizational | AI amplifies existing system (good or bad) |
| SPACE Framework | SPACE Research | Academic publication | Multi-dimensional productivity |
| Synthetic Customers | arXiv 2609.30137 (Nubank) | Peer-reviewed (2026) | Simulation + production validation |
| Anthropic Harness | Anthropic blog (Nov 2025) | Industry practice | Long-running agent patterns |
| OpenAI Agents | OpenAI blog (Aug 2026) | Industry practice | Harness engineering |

### 3.4 Regulatory References

(Context only; reference implementation uses synthetic data)

| Regulation | Jurisdiction | Relevance |
|------------|--------------|-----------|
| CMN 4.893 (amended 5.274/2025) | Brazil (Banco Central) | Cybersecurity in financial institutions |
| LGPD | Brazil (ANPD) | Data protection and privacy |
| NIST AI RMF | USA (NIST) | AI risk management framework |

**Note**: Reference implementation does NOT process regulated data. These are cited to demonstrate governance patterns relevant to enterprise adoption.

## 4. Source Quality Assessment

### 4.1 Evidence Classification

**Tier 1: Strong Evidence**
- Peer-reviewed research (DORA, METR, SPACE)
- Official standards (OpenTelemetry, JSON Schema, OWASP)
- Official documentation (PyPA, Docker, GitHub)
- Regulatory text (CMN, LGPD, NIST)

**Tier 2: Strong Industrial Practice**
- Anthropic long-running agents (detailed technical write-up)
- OpenAI harness engineering (detailed technical write-up)
- Nubank synthetic customers (peer-reviewed arXiv paper)

**Tier 3: Self-Reported Cases**
- Revolut cost reduction (~90% inference cost, not TCO)
- Vendor-provided benchmarks (evaluate critically)

**Tier 4: Conceptual Models**
- Boris Cherny archetypes (useful mental model, not proven)
- Katelyn Lesse agent workload analysis (engineering insight, not research)

### 4.2 Source Limitations

**Language Barrier**: SOURCE-01, SOURCE-02, SOURCE-03 are in Portuguese. Technical content is language-agnostic, but nuance may be lost in interpretation.

**Recency**: AI field moves rapidly. Sources from 2025-2026 are current but may become outdated.

**Generalizability**: DORA research is broad; individual case studies (Nubank, Revolut) may not generalize.

**Experimental Projects**: Harness², Scion marked as pre-1.0 or experimental. Use patterns, not direct dependencies.

**Missing Project Brief**: SOURCE-04 absence requires assumption-based planning with user validation.

## 5. Source Reconciliation

### 5.1 Apparent Contradictions

**Contradiction 1**: "AI increases productivity" vs "METR found 19% time increase"

**Reconciliation**: Effect is context-dependent. Task type, developer experience, tooling quality, and system maturity all moderate outcomes. Reference implementation should measure rather than assume productivity gain.

**Contradiction 2**: "Multiagent collaboration" vs "Start with single agent"

**Reconciliation**: SOURCE-01 and Anthropic both recommend starting simple and adding agents only when specialization justifies complexity. Reference implementation follows graduated approach: MVP with 3-5 agents, EV1 adds collaboration.

**Contradiction 3**: "Open-weight cost savings" vs "Complex TCO analysis"

**Reconciliation**: Revolut case shows inference cost reduction, not total cost. Reference implementation should model gateway with routing logic, not prescribe open-weight as universally cheaper.

### 5.2 Naming Clarifications

**Harness² Confusion**: Original documents implied two separate Google projects. SOURCE-03 clarifies:
- `google-research/harness2` = harness engineering
- `google/elephant-goldfish-model` = specification quality / review
- `GoogleCloudPlatform/scion` = multi-agent orchestration

These are complementary, not competing.

**Pi and Prime**: SOURCE-02 references "Pi" and "Prime" without explicit URLs. Research identified:
- `earendil-works/pi` (harness framework)
- `PrimeIntellect-ai/prime-agent` (continual harness)

Both are MIT-licensed and align with patterns described.

## 6. Assumptions Requiring Validation

**[ASSUMPTION-001]**: Scope is public reference implementation, not production banking software.

**[ASSUMPTION-002]**: Primary users are engineering teams, AI researchers, enterprise architects.

**[ASSUMPTION-003]**: Deployment target is local demonstration with synthetic data.

**[ASSUMPTION-004]**: Technology stack is Python 3.12+ (aligns with Harness² and agent tooling).

**[ASSUMPTION-005]**: Outcome is runnable demonstration of complete value stream run.

**[ASSUMPTION-006]**: License intent is Apache 2.0 (subject to legal review).

**[ASSUMPTION-007]**: Maintenance model is community-driven (not corporate product).

**[ASSUMPTION-008]**: Performance target is "demonstrable" not "production-scale" (< 15 min value stream run acceptable).

**[ASSUMPTION-009]**: MVP scope is single-machine, not distributed.

**[ASSUMPTION-010]**: English is the implementation language (code, comments, docs).

**Action Required**: User must explicitly confirm or correct assumptions before prompt execution begins.

## 7. Source Versioning

This planning package is based on sources as of **2026-11-18**. Key version pins:

- OpenTelemetry: v1.45.0
- JSON Schema: 2020-12
- OWASP LLM Top 10: 2025 edition
- Python: 3.12+ required
- Cedar: v4.6

If re-executing this plan after significant time has passed, verify:
1. OpenTelemetry semantic conventions have not changed
2. Security standards (OWASP) are current
3. Referenced repositories are still maintained
4. Licenses have not changed

## 8. Citation Format

Throughout planning documents, sources are cited as:

- **[SOURCE-01]** = Strategic playbook
- **[SOURCE-02]** = Implementation blueprint
- **[SOURCE-03]** = Harness² clarification
- **[RESEARCH]** = Engineering research agent findings
- **[OWASP]** = OWASP LLM Top 10
- **[DORA]** = DORA research
- **[PROJECT:name]** = Specific open-source project

## 9. Evidence Gaps

The following questions remain open and should be addressed through implementation and measurement:

**Gap 1**: Optimal agent granularity (when to split vs combine responsibilities)

**Gap 2**: Cost/quality trade-offs for different model routing strategies

**Gap 3**: Human-agent collaboration patterns that maximize outcome quality

**Gap 4**: Effectiveness of trajectory evaluations vs outcome evaluations

**Gap 5**: Adoption patterns that lead to measurable organizational improvement

**Gap 6**: Security control effectiveness in real-world prompt injection scenarios

**Gap 7**: Scalability limits of document-based context (when to upgrade to RAG/graph)

The reference implementation should be instrumented to gather evidence on these questions.

## 10. Living Document Status

This source register is **frozen at planning time** (2026-11-18). If new authoritative sources emerge during implementation, they should be:

1. Added to an addendum section
2. Justified with rationale for inclusion
3. Cross-referenced to affected requirements and prompts
4. Documented in git commit messages

Do not silently update sources without traceability.

---

**Document Version**: 1.0.0  
**Status**: Authoritative Source Catalog  
**Last Updated**: 2026-11-18  
**Freeze Date**: 2026-11-18 (no updates without formal addendum)
