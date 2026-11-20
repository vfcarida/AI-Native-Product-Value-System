"""
GitHub Spec-Kit inspired specification template library (EVO-020).

Provides 5 built-in templates (User Story, Scenario/Gherkin, API Contract,
Non-Functional Requirement, and Lightweight Decision Record) with custom
directory overrides and placeholder substitution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path


class TemplateType(str, Enum):
    """Supported Spec-Kit specification template types."""

    USER_STORY = "user_story"
    SCENARIO = "scenario"
    API_CONTRACT = "api_contract"
    NFR = "non_functional_requirement"
    DECISION = "decision"

    @classmethod
    def from_cli(cls, raw: str) -> TemplateType:
        """Resolve CLI aliases such as 'user-story', 'api-contract', or 'nfr'."""
        normalized = raw.strip().lower().replace("-", "_")
        aliases = {
            "user_story": cls.USER_STORY,
            "story": cls.USER_STORY,
            "us": cls.USER_STORY,
            "scenario": cls.SCENARIO,
            "bdd": cls.SCENARIO,
            "api_contract": cls.API_CONTRACT,
            "api": cls.API_CONTRACT,
            "nfr": cls.NFR,
            "non_functional_requirement": cls.NFR,
            "non_functional": cls.NFR,
            "decision": cls.DECISION,
            "adr": cls.DECISION,
        }
        if normalized not in aliases:
            valid = ", ".join(sorted({"user-story", "scenario", "api-contract", "nfr", "decision"}))
            raise ValueError(f"Unknown spec template type {raw!r}; expected one of: {valid}")
        return aliases[normalized]


@dataclass
class Template:
    """Reusable Markdown specification template."""

    type: TemplateType
    name: str
    content: str
    variables: dict[str, str] = field(default_factory=dict)
    id_prefix: str = "SPEC"


class TemplateLibrary:
    """Built-in and project-customizable Spec-Kit template catalog."""

    def __init__(self, template_dir: str | Path | None = None) -> None:
        self.template_dir = Path(template_dir) if template_dir else None
        self._templates = self._load_builtin_templates()
        if self.template_dir is not None and self.template_dir.exists():
            self._templates.update(self._load_custom_templates(self.template_dir))

    def _load_builtin_templates(self) -> dict[TemplateType, Template]:
        return {
            TemplateType.USER_STORY: Template(
                type=TemplateType.USER_STORY,
                name="User Story",
                content=self._get_user_story_template(),
                variables={
                    "NUMBER": "Story number (e.g., 042)",
                    "TITLE": "Story title",
                    "USER_ROLE": "User role (e.g., customer, admin)",
                    "GOAL": "What user wants to accomplish",
                    "BENEFIT": "Business or user value achieved",
                    "STORY_POINTS": "Relative effort estimate",
                },
                id_prefix="US",
            ),
            TemplateType.SCENARIO: Template(
                type=TemplateType.SCENARIO,
                name="Scenario Specification",
                content=self._get_scenario_template(),
                variables={
                    "NUMBER": "Scenario number (e.g., 015)",
                    "FEATURE_NAME": "Feature name",
                    "SCENARIO_NAME": "Primary happy-path scenario title",
                },
                id_prefix="SCENARIO",
            ),
            TemplateType.API_CONTRACT: Template(
                type=TemplateType.API_CONTRACT,
                name="API Contract",
                content=self._get_api_contract_template(),
                variables={
                    "NUMBER": "API contract number (e.g., 001)",
                    "ENDPOINT_NAME": "Endpoint display name",
                    "METHOD": "HTTP method (GET, POST, PUT, DELETE)",
                    "PATH": "API path segment (e.g., transfers)",
                },
                id_prefix="API",
            ),
            TemplateType.NFR: Template(
                type=TemplateType.NFR,
                name="Non-Functional Requirement",
                content=self._get_nfr_template(),
                variables={
                    "NUMBER": "NFR number (e.g., 001)",
                    "TITLE": "Non-functional requirement title",
                    "CATEGORY": "Category (performance, security, reliability, accessibility)",
                },
                id_prefix="NFR",
            ),
            TemplateType.DECISION: Template(
                type=TemplateType.DECISION,
                name="Lightweight Decision Record",
                content=self._get_decision_template(),
                variables={
                    "NUMBER": "Decision number (e.g., 001)",
                    "TITLE": "Decision title",
                },
                id_prefix="DEC",
            ),
        }

    def _load_custom_templates(self, directory: Path) -> dict[TemplateType, Template]:
        overrides: dict[TemplateType, Template] = {}
        for ttype in TemplateType:
            for candidate in (
                directory / f"{ttype.value}.md",
                directory / f"{ttype.value.replace('_', '-')}.md",
            ):
                if candidate.exists():
                    base = self._templates[ttype]
                    overrides[ttype] = Template(
                        type=ttype,
                        name=base.name,
                        content=candidate.read_text(encoding="utf-8"),
                        variables=dict(base.variables),
                        id_prefix=base.id_prefix,
                    )
                    break
        return overrides

    def get_template(self, template_type: TemplateType | str) -> Template:
        """Retrieve template definition by ``TemplateType`` or CLI string."""
        resolved = (
            template_type
            if isinstance(template_type, TemplateType)
            else TemplateType.from_cli(template_type)
        )
        return self._templates[resolved]

    def list_templates(self) -> list[Template]:
        """Return all available templates."""
        return list(self._templates.values())

    def render_template(
        self,
        template_type: TemplateType | str,
        variables: dict[str, str],
    ) -> str:
        """Render template replacing ``[PLACEHOLDER]`` tokens with supplied values."""
        template = self.get_template(template_type)
        content = template.content
        merged = {
            "TODAY": datetime.now(UTC).strftime("%Y-%m-%d"),
            **variables,
        }
        for key, value in merged.items():
            content = content.replace(f"[{key}]", str(value))
        return content

    def _get_user_story_template(self) -> str:
        return """# User Story: [TITLE]

**ID**: US-[NUMBER]
**Status**: Draft
**Priority**: Medium
**Estimate**: [STORY_POINTS]

## Story

As a **[USER_ROLE]**
I want **[GOAL]**
So that **[BENEFIT]**

## Context

[Background information, problem statement, user pain points]

## Acceptance Criteria

- [ ] **Given** [precondition 1]
      **When** [action 1]
      **Then** [expected outcome 1]

- [ ] **Given** [precondition 2]
      **When** [action 2]
      **Then** [expected outcome 2]

- [ ] **Given** [error precondition]
      **When** [failure action]
      **Then** [graceful error handling]

## Scenarios

### Scenario 1: Happy Path

**Given**: [setup conditions]
**When**: [user action]
**Then**: [expected result]
**And**: [additional assertions]

### Scenario 2: Error Case

**Given**: [setup conditions]
**When**: [invalid or failing action]
**Then**: [safe error feedback and recovery option]

## Non-Functional Requirements

- **Performance**: [p95 latency / throughput target]
- **Security**: [authentication, authorization, data protection]
- **Accessibility**: [WCAG 2.1 AA / keyboard navigation]
- **Analytics**: [events to emit for outcome measurement]

## Out of Scope

- [Explicitly list what this story does NOT include]

## Related

- **Requirements**: REQ-[NUMBER]
- **Customer Feedback**: []
- **Dependencies**: []
- **Implementation**: []
- **Tests**: []

## Notes

[Additional context, design decisions, open questions]

---

**Metadata**:
```yaml
id: US-[NUMBER]
type: user_story
status: draft
priority: medium
estimate: 5
created: "[TODAY]"
updated: "[TODAY]"
```
"""

    def _get_scenario_template(self) -> str:
        return """# Scenario: [FEATURE_NAME]

**ID**: SCENARIO-[NUMBER]
**Feature**: [FEATURE_NAME]
**Status**: Draft

## Background

[Common preconditions shared across all scenarios in this feature]

## Scenarios

### Scenario: [SCENARIO_NAME]

```gherkin
Feature: [FEATURE_NAME]

  Scenario: [SCENARIO_NAME]
    Given [precondition 1]
    And [precondition 2]
    When [action performed]
    Then [expected outcome]
    And [additional verification]

  Scenario: Error handling for [SCENARIO_NAME]
    Given [precondition]
    When [error condition occurs]
    Then [graceful error response is returned]
    And [user receives actionable feedback]
```

## Data Tables

| Input | Condition | Expected Result | Error Message |
| :--- | :--- | :--- | :--- |
| Valid | Nominal | Success | - |
| Invalid | Threshold violated | Failure | "Validation failed" |

## State Transitions

```text
IDLE -> initiate -> VALIDATING
VALIDATING -> success -> PROCESSING
VALIDATING -> failure -> FAILED
PROCESSING -> complete -> COMPLETED
```

## Edge Cases

1. **Transient timeout**
   - Given: Request in flight
   - When: Upstream timeout occurs
   - Then: Retry safely with idempotency key

2. **Duplicate concurrent request**
   - Given: Request already in progress
   - When: Identical request arrives concurrently
   - Then: Deduplicate without side effects

## Related

- **User Story**: US-[NUMBER]
- **Implementation**: []
- **Tests**: []

---

**Metadata**:
```yaml
id: SCENARIO-[NUMBER]
type: scenario
feature: "[FEATURE_NAME]"
status: draft
test_framework: pytest
created: "[TODAY]"
```
"""

    def _get_api_contract_template(self) -> str:
        return """# API Contract: [ENDPOINT_NAME]

**ID**: API-[NUMBER]
**Endpoint**: `[METHOD] /api/v1/[PATH]`
**Version**: v1
**Status**: Draft

## Overview

[Brief description of what this endpoint does]

## Authentication

**Required**: Yes
**Method**: Bearer Token
**Scopes**: `read`, `write`

## Request

### Query Parameters

| Parameter | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `limit` | integer | No | 20 | Max items per page |

### Request Body

**Content-Type**: `application/json`

```json
{
  "field": "value"
}
```

## Response

### Success (200 OK)

```json
{
  "id": "res_123",
  "status": "completed"
}
```

### Error Responses

#### 400 Bad Request
```json
{
  "error": {
    "code": "invalid_request",
    "message": "Field validation failed"
  }
}
```

#### 401 Unauthorized
```json
{
  "error": {
    "code": "authentication_failed",
    "message": "Invalid or expired token"
  }
}
```

## Examples

### cURL

```bash
curl -X [METHOD] https://api.example.com/api/v1/[PATH] \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"field": "value"}'
```

## Rate Limiting & Idempotency

- **Rate Limit**: 100 requests/min per caller
- **Idempotency**: Supported via `Idempotency-Key` header (24h TTL)

## Related

- **User Story**: US-[NUMBER]
- **Implementation**: []
- **Tests**: []

---

**Metadata**:
```yaml
id: API-[NUMBER]
type: api_contract
endpoint: "[METHOD] /api/v1/[PATH]"
version: v1
status: draft
created: "[TODAY]"
```
"""

    def _get_nfr_template(self) -> str:
        return """# Non-Functional Requirement: [TITLE]

**ID**: NFR-[NUMBER]
**Category**: [CATEGORY]
**Status**: Draft

## Requirements

- **Latency**: p95 response time <= 500ms under 200 req/s
- **Availability**: >= 99.9% monthly uptime (SLO error budget 0.1%)
- **Security**: TLS 1.3 in transit, zero secrets in logs, Cedar authorization enforced
- **Reliability**: Graceful degradation and automated rollback on SLO breach

## Verification

- [ ] Load test confirms p95 latency <= 500ms at target concurrency
- [ ] Security audit validates authentication and input sanitization
- [ ] Prometheus SLO alerts configured and tested

## Related

- **Requirements**: REQ-[NUMBER]
- **Implementation**: []
- **Tests**: []

---

**Metadata**:
```yaml
id: NFR-[NUMBER]
type: non_functional_requirement
category: "[CATEGORY]"
status: draft
created: "[TODAY]"
```
"""

    def _get_decision_template(self) -> str:
        return """# Decision Record: [TITLE]

**ID**: DEC-[NUMBER]
**Status**: Proposed
**Date**: [TODAY]

## Context

[Describe the architectural or product trade-off requiring a decision]

## Decision

[State the decision concisely]

## Alternatives Considered

- **Option A**: [Pros / Cons]
- **Option B**: [Pros / Cons]

## Consequences

- **Positive**: [Benefits gained]
- **Negative**: [Trade-offs accepted]

## Related

- **Full ADR**: []
- **Implementation**: []
- **Tests**: []

---

**Metadata**:
```yaml
id: DEC-[NUMBER]
type: decision
status: proposed
created: "[TODAY]"
```
"""
