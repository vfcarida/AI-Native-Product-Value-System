# Developer Guide: Adding Custom Review Agents

Extend the multi-agent review system by subclassing `BaseReviewAgent` ([`src/product_value_system/agents/base_review_agent.py`](../../src/product_value_system/agents/base_review_agent.py)).

---

## 1. Subclass `BaseReviewAgent`

```python
from typing import Any
from product_value_system.agents.base_review_agent import BaseReviewAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.review.models import FindingCategory


class AccessibilityReviewAgent(BaseReviewAgent):
    review_type = "accessibility"
    evidence_type = "review"
    default_category = FindingCategory.MAINTAINABILITY

    def __init__(self, schema_validator: SchemaValidator) -> None:
        super().__init__(
            schema_validator,
            name="accessibility-review-agent",
            version="0.1.0",
            capabilities=["accessibility_review", "wcag_audit"],
        )

    def _build_system_prompt(self) -> str:
        return (
            "You are an independent WCAG 2.2 AA accessibility reviewer. "
            "Identify keyboard navigation, contrast, ARIA, and screen-reader gaps."
        )

    def _build_review_prompt(self, spec: dict[str, Any]) -> str:
        return f"Review specification {spec['id']} for accessibility compliance. Respond in JSON."
```

`BaseReviewAgent.execute()` automatically handles:
- Input validation against `specification.schema.json`
- Goldfish memory clearing (`harness.memory.clear()`)
- Flexible parsing of JSON objects (`{"findings": [...], "overall_verdict": ...}`) or JSON finding arrays
- Output validation against `evidence.schema.json`

---

## 2. Register with `MultiAgentReviewOrchestrator`

```python
from product_value_system.review import MultiAgentReviewOrchestrator

orchestrator = MultiAgentReviewOrchestrator(
    schema_validator=validator,
    extra_reviewers=[AccessibilityReviewAgent(validator)],
)
consolidated = await orchestrator.execute(spec)
```

Your custom reviewer executes concurrently via `asyncio.gather()` inside its own cleared-memory `Harness` and feeds findings directly into `ReviewSynthesizer`.
