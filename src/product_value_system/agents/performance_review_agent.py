"""
Performance review agent: Independent latency, complexity, and resource review (EVO-013).
"""

import json
from typing import Any

from product_value_system.agents.base_review_agent import BaseReviewAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.review.models import FindingCategory


class PerformanceReviewAgent(BaseReviewAgent):
    """
    Independent performance reviewer operating in Goldfish (cleared memory) mode.

    Checklist:
    - Algorithmic complexity (O(n^2) loops, nested queries, unindexed lookups)
    - Resource limits (memory streaming/pagination, CPU, disk I/O)
    - Network efficiency (N+1 queries, batching, connection pooling)
    - Caching strategy and invalidation boundaries
    - Scalability under peak concurrency and data growth
    - Latency budgets (p50, p95, p99) and timeout configurations
    """

    review_type = "performance"
    evidence_type = "review"
    default_category = FindingCategory.PERFORMANCE

    def __init__(self, schema_validator: SchemaValidator) -> None:
        super().__init__(
            schema_validator,
            name="performance-review-agent",
            version="0.1.0",
            capabilities=["performance_review", "complexity_analysis", "scalability_assessment"],
            required_tools=["schema.validate", "benchmark.lookup"],
        )

    def _build_system_prompt(self) -> str:
        """Build system prompt focused on performance bottlenecks and resource efficiency."""
        return (
            "You are an independent performance engineering reviewer operating in "
            "fresh-context (Goldfish) mode.\n\n"
            "Focus areas:\n"
            "1. Algorithmic complexity: O(n^2)/O(n^3) loops, redundant scans, inefficient "
            "data structures.\n"
            "2. Resource usage: unbounded in-memory loads (require pagination/streaming), "
            "excessive file/network I/O, N+1 queries.\n"
            "3. Scalability: concurrency limits, database indexing, connection pooling, "
            "caching strategy and TTLs.\n"
            "4. Latency budgets: p50/p95/p99 targets, timeouts, async vs synchronous I/O.\n\n"
            "Severity calibration:\n"
            "- CRITICAL: Guaranteed OOM crash, deadlock, or outage at stated target load\n"
            "- HIGH: >10x latency/memory bottleneck or unindexed hot path\n"
            "- MEDIUM: 2-10x inefficiency or missing cache/pooling guidance\n"
            "- LOW: Minor optimization opportunity (<2x impact)\n\n"
            "Cite requirement IDs (FR-###) and quantify estimated impact where possible."
        )

    def _build_review_prompt(self, spec: dict[str, Any]) -> str:
        """Build user prompt with specification details for performance review."""
        reqs_text = "\n".join(
            f"- {r['id']}: {r['description']} (priority: {r.get('priority', 'must')})"
            for r in spec["functional_requirements"]
        )
        decisions_text = "\n".join(
            f"- {d['id']}: {d['decision']} (rationale: {d['rationale']})"
            for d in spec.get("architecture_decisions", [])
        )
        return f"""Conduct an independent performance review of this specification:

## Specification ID
{spec['id']}

## Functional Requirements
{reqs_text}

## Architecture Decisions
{decisions_text or '(none specified)'}

## Quality Attributes
{json.dumps(spec.get('quality_attributes', {}), indent=2)}

## Constraints
{json.dumps(spec.get('constraints', {}), indent=2)}

## Performance Review Task
Identify bottlenecks across algorithmic complexity, memory usage, N+1 I/O, caching,
connection pooling, concurrency, and latency budgets.

Respond in JSON format with `findings`, `overall_verdict`, and `summary` (or a JSON array
of finding objects with `severity`, `category`, `title`, `description`, `recommendation`,
`requirement_id`, and `estimated_impact`)."""
