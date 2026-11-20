"""
Structured review data models for multi-agent independent review (EVO-013).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


class FindingSeverity(str, Enum):
    """Severity taxonomy for review findings."""

    CRITICAL = "CRITICAL"  # Blocks release
    HIGH = "HIGH"  # Must fix before launch
    MEDIUM = "MEDIUM"  # Should fix, can defer
    LOW = "LOW"  # Nice-to-have

    @property
    def rank(self) -> int:
        """Numeric severity rank (higher means more severe)."""
        order = {
            FindingSeverity.LOW: 1,
            FindingSeverity.MEDIUM: 2,
            FindingSeverity.HIGH: 3,
            FindingSeverity.CRITICAL: 4,
        }
        return order[self]

    @classmethod
    def from_raw(cls, value: str | FindingSeverity) -> FindingSeverity:
        """Normalize case-insensitive severity strings into ``FindingSeverity``."""
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().upper()
        try:
            return cls[normalized]
        except KeyError:
            return cls.MEDIUM


class FindingCategory(str, Enum):
    """Domain taxonomy for review findings."""

    SECURITY = "security"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    ARCHITECTURE = "architecture"
    SCALABILITY = "scalability"
    OBSERVABILITY = "observability"

    @classmethod
    def from_raw(
        cls,
        value: str | FindingCategory,
        *,
        default: FindingCategory | None = None,
    ) -> FindingCategory:
        """Map domain-specific category names into the canonical ``FindingCategory`` enum."""
        resolved_default = default if default is not None else cls.ARCHITECTURE
        if isinstance(value, cls):
            return value
        low = str(value).strip().lower()
        for member in cls:
            if low == member.value or low == member.name.lower():
                return member

        security_aliases = {
            "auth",
            "authentication",
            "authorization",
            "access-control",
            "access_control",
            "injection",
            "sql",
            "xss",
            "csrf",
            "ssrf",
            "cryptography",
            "encryption",
            "secrets",
            "privacy",
            "pii",
            "hipaa",
            "gdpr",
            "sandbox",
            "privilege-escalation",
            "owasp",
            "input-validation",
        }
        perf_aliases = {
            "latency",
            "throughput",
            "complexity",
            "memory",
            "caching",
            "profiling",
            "bottleneck",
            "n+1",
            "resource-usage",
            "resource-exhaustion",
        }
        maint_aliases = {
            "clarity",
            "testability",
            "testing",
            "coverage",
            "documentation",
            "naming",
            "technical-debt",
            "debt",
            "readability",
            "modularity",
        }
        scale_aliases = {"scaling", "concurrency", "sharding", "partitioning", "burst"}
        obs_aliases = {"logging", "tracing", "metrics", "monitoring", "audit", "telemetry"}

        if low in security_aliases:
            return cls.SECURITY
        if low in perf_aliases:
            return cls.PERFORMANCE
        if low in maint_aliases:
            return cls.MAINTAINABILITY
        if low in scale_aliases:
            return cls.SCALABILITY
        if low in obs_aliases:
            return cls.OBSERVABILITY
        return resolved_default


@dataclass
class ReviewFinding:
    """Single structured finding emitted by an independent review agent."""

    id: str
    reviewer: str
    severity: FindingSeverity
    category: FindingCategory
    description: str
    recommendation: str
    requirement_id: str | None = None
    code_location: str | None = None
    evidence: str | None = None
    title: str | None = None
    estimated_impact: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize finding to dictionary."""
        payload: dict[str, Any] = {
            "id": self.id,
            "reviewer": self.reviewer,
            "severity": self.severity.value,
            "category": self.category.value,
            "title": self.title or self.description[:80],
            "description": self.description,
            "recommendation": self.recommendation,
            "requirement_id": self.requirement_id,
            "code_location": self.code_location,
            "evidence": self.evidence,
        }
        if self.estimated_impact is not None:
            payload["estimated_impact"] = self.estimated_impact
        return payload

    def to_evidence_finding(self) -> dict[str, Any]:
        """Serialize finding to ``evidence.schema.json`` finding object format."""
        refs = [self.requirement_id] if self.requirement_id else []
        return {
            "severity": self.severity.value.lower(),
            "category": self.category.value,
            "title": self.title or self.description[:80],
            "description": self.description,
            "recommendation": self.recommendation,
            "references": refs,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        default_reviewer: str = "reviewer",
        default_category: FindingCategory = FindingCategory.ARCHITECTURE,
    ) -> "ReviewFinding":
        """Parse finding from either EVO-013 format or legacy ``evidence.schema.json`` format."""
        reviewer = str(data.get("reviewer") or default_reviewer)
        raw_refs = data.get("references")
        req_id = data.get("requirement_id")
        if req_id is None and isinstance(raw_refs, list) and raw_refs:
            req_id = str(raw_refs[0])

        prefix = reviewer.split("-")[0] if reviewer else "finding"
        finding_id = str(data.get("id") or f"{prefix}-{uuid4().hex[:8]}")

        return cls(
            id=finding_id,
            reviewer=reviewer,
            severity=FindingSeverity.from_raw(data.get("severity", "MEDIUM")),
            category=FindingCategory.from_raw(
                data.get("category", default_category),
                default=default_category,
            ),
            title=str(data["title"]) if data.get("title") else None,
            description=str(data.get("description", "")),
            recommendation=str(data.get("recommendation", "")),
            requirement_id=str(req_id) if req_id is not None else None,
            code_location=str(data["code_location"]) if data.get("code_location") else None,
            evidence=str(data["evidence"]) if data.get("evidence") else None,
            estimated_impact=(
                str(data["estimated_impact"]) if data.get("estimated_impact") else None
            ),
        )


@dataclass
class ReviewConflict:
    """Conflicting recommendations identified between two independent reviewers."""

    finding_a: ReviewFinding
    finding_b: ReviewFinding
    conflict_type: str
    resolution: str
    chosen_recommendation: str
    rationale: str
    requirement_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize conflict record to dictionary."""
        return {
            "finding_a_id": self.finding_a.id,
            "finding_b_id": self.finding_b.id,
            "reviewer_a": self.finding_a.reviewer,
            "reviewer_b": self.finding_b.reviewer,
            "requirement_id": self.requirement_id or self.finding_a.requirement_id,
            "conflict_type": self.conflict_type,
            "resolution": self.resolution,
            "chosen_recommendation": self.chosen_recommendation,
            "rationale": self.rationale,
        }


@dataclass
class ReviewResult:
    """Consolidated multi-agent review report and release gate decision."""

    review_id: str
    timestamp: datetime
    specification_id: str
    verdict: str  # "APPROVED", "APPROVED_WITH_CONCERNS", "REJECTED"

    findings: list[ReviewFinding]
    conflicts: list[ReviewConflict]
    summary: dict[str, int]

    gate_decision: str  # "PASS", "FAIL"
    gate_reason: str

    reviewers_executed: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    total_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize consolidated review result to JSON/YAML-friendly dictionary."""
        return {
            "review_id": self.review_id,
            "timestamp": self.timestamp.isoformat(),
            "specification_id": self.specification_id,
            "verdict": self.verdict,
            "gate_decision": self.gate_decision,
            "gate_reason": self.gate_reason,
            "summary": dict(self.summary),
            "reviewers_executed": list(self.reviewers_executed),
            "duration_seconds": round(self.duration_seconds, 4),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "findings": [f.to_dict() for f in self.findings],
            "conflicts": [c.to_dict() for c in self.conflicts],
        }

    def to_evidence_artifact(self) -> dict[str, Any]:
        """Convert consolidated review to a schema-valid ``evidence.schema.json`` artifact."""
        verdict_map = {
            "APPROVED": "approve",
            "APPROVED_WITH_CONCERNS": "approve_with_concerns",
            "REJECTED": "reject",
        }
        safe_spec = self.specification_id.lower().replace("_", "-")
        valid_prefixes = ("evidence-", "spec-", "build-", "release-", "run-", "outcome-")
        if not safe_spec.startswith(valid_prefixes):
            evidence_id = f"evidence-review-{safe_spec}"
        else:
            evidence_id = f"{safe_spec}-multi-review"

        return {
            "id": evidence_id,
            "version": 1,
            "created": self.timestamp.isoformat(),
            "type": "review",
            "artifact_id": self.specification_id,
            "verdict": verdict_map.get(self.verdict, "reject"),
            "summary": (
                f"Multi-agent review ({', '.join(self.reviewers_executed)}): "
                f"{self.gate_decision} — {self.gate_reason}"
            ),
            "findings": [f.to_evidence_finding() for f in self.findings],
            "reviewer": "multi-agent-review-synthesizer v0.1.0",
        }
