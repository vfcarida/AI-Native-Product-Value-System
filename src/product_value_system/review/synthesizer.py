"""
Multi-agent review synthesis, conflict detection, and resolution engine (EVO-013).
"""

import json
import logging
from datetime import UTC, datetime
from itertools import combinations
from typing import Any
from uuid import uuid4

from product_value_system.adapters.model_adapter import Message, ModelAdapter, Role
from product_value_system.review.models import (
    FindingCategory,
    FindingSeverity,
    ReviewConflict,
    ReviewFinding,
    ReviewResult,
)

logger = logging.getLogger(__name__)

# Domain precedence on equal severity: Security > Architecture > Performance > ... > Maintainability
CATEGORY_PRIORITY: list[FindingCategory] = [
    FindingCategory.SECURITY,
    FindingCategory.ARCHITECTURE,
    FindingCategory.PERFORMANCE,
    FindingCategory.SCALABILITY,
    FindingCategory.OBSERVABILITY,
    FindingCategory.MAINTAINABILITY,
]

# Contradictory recommendation keyword pairs grouped by tradeoff pattern
CONFLICT_KEYWORD_PAIRS: dict[tuple[FindingCategory, FindingCategory], list[tuple[str, str]]] = {
    (FindingCategory.SECURITY, FindingCategory.PERFORMANCE): [
        ("encrypt", "skip"),
        ("encrypt", "unencrypted"),
        ("encrypt", "plaintext"),
        ("encrypt", "disable"),
        ("validate", "bypass"),
        ("validate", "skip"),
        ("authenticate", "bypass"),
        ("authenticate", "skip"),
        ("audit", "skip"),
        ("strict", "bypass"),
        ("add", "remove"),
        ("fresh", "cache"),
        ("no-cache", "cache"),
    ],
    (FindingCategory.PERFORMANCE, FindingCategory.MAINTAINABILITY): [
        ("inline", "extract"),
        ("inline", "modularize"),
        ("denormalize", "normalize"),
        ("cache", "simplify"),
        ("cache", "remove"),
        ("optimize", "simplify"),
        ("custom", "standard"),
        ("low-level", "abstract"),
        ("add", "remove"),
    ],
    (FindingCategory.SECURITY, FindingCategory.MAINTAINABILITY): [
        ("isolate", "merge"),
        ("separate", "consolidate"),
        ("strict", "simplify"),
        ("rbac", "single-role"),
        ("encrypt", "simplify"),
        ("add", "remove"),
    ],
    (FindingCategory.ARCHITECTURE, FindingCategory.PERFORMANCE): [
        ("decouple", "inline"),
        ("microservice", "monolith"),
        ("async", "synchronous"),
        ("normalize", "denormalize"),
        ("event-driven", "direct"),
        ("add", "remove"),
    ],
}


class ReviewSynthesizer:
    """
    Aggregates findings from independent specialized reviewers, detects and
    resolves inter-reviewer recommendation conflicts, prioritizes actions, and
    produces a deterministic release gate decision.
    """

    def __init__(
        self,
        *,
        max_high_findings: int = 5,
        llm_judge: ModelAdapter | None = None,
    ) -> None:
        self.max_high_findings = max_high_findings
        self.llm_judge = llm_judge

    async def synthesize(
        self,
        security_result: dict[str, Any] | None = None,
        performance_result: dict[str, Any] | None = None,
        maintainability_result: dict[str, Any] | None = None,
        architecture_result: dict[str, Any] | None = None,
        *,
        reviewer_outputs: list[dict[str, Any]] | None = None,
        specification_id: str | None = None,
        duration_seconds: float = 0.0,
        total_cost_usd: float = 0.0,
    ) -> ReviewResult:
        """
        Synthesize independent reviewer outputs into a consolidated ``ReviewResult``.

        Accepts either the 4 standard positional/keyword dicts or an arbitrary
        ``reviewer_outputs`` list (supporting custom reviewers).
        """
        sources: list[tuple[dict[str, Any], str, FindingCategory]] = []
        named_pairs = [
            (security_result, "security-review-agent", FindingCategory.SECURITY),
            (performance_result, "performance-review-agent", FindingCategory.PERFORMANCE),
            (
                maintainability_result,
                "maintainability-review-agent",
                FindingCategory.MAINTAINABILITY,
            ),
            (architecture_result, "architecture-review-agent", FindingCategory.ARCHITECTURE),
        ]
        for res, default_name, default_cat in named_pairs:
            if res is not None:
                sources.append((res, default_name, default_cat))

        if reviewer_outputs:
            for extra in reviewer_outputs:
                rev_name = str(extra.get("reviewer", "custom-reviewer"))
                sources.append((extra, rev_name, FindingCategory.ARCHITECTURE))

        resolved_spec_id = specification_id
        reviewers_executed: list[str] = []
        all_findings: list[ReviewFinding] = []

        for source_dict, default_name, default_cat in sources:
            reviewer_label = str(source_dict.get("reviewer") or default_name).split()[0]
            if reviewer_label not in reviewers_executed:
                reviewers_executed.append(reviewer_label)

            if resolved_spec_id is None:
                candidate_spec = source_dict.get("artifact_id") or source_dict.get(
                    "specification_id"
                )
                if candidate_spec:
                    resolved_spec_id = str(candidate_spec)

            for raw_finding in source_dict.get("findings", []):
                if isinstance(raw_finding, ReviewFinding):
                    all_findings.append(raw_finding)
                elif isinstance(raw_finding, dict):
                    all_findings.append(
                        ReviewFinding.from_dict(
                            raw_finding,
                            default_reviewer=reviewer_label,
                            default_category=default_cat,
                        )
                    )

        # Sort prioritized findings: CRITICAL -> LOW, then category precedence
        all_findings.sort(
            key=lambda f: (
                -f.severity.rank,
                CATEGORY_PRIORITY.index(f.category)
                if f.category in CATEGORY_PRIORITY
                else len(CATEGORY_PRIORITY),
                f.id,
            )
        )

        # Detect and resolve inter-reviewer conflicts
        raw_conflicts = self._detect_conflicts(all_findings)
        resolved_conflicts: list[ReviewConflict] = []
        for pair in raw_conflicts:
            resolved_conflicts.append(await self._resolve_conflict(pair))

        summary = {
            "CRITICAL": sum(1 for f in all_findings if f.severity == FindingSeverity.CRITICAL),
            "HIGH": sum(1 for f in all_findings if f.severity == FindingSeverity.HIGH),
            "MEDIUM": sum(1 for f in all_findings if f.severity == FindingSeverity.MEDIUM),
            "LOW": sum(1 for f in all_findings if f.severity == FindingSeverity.LOW),
        }

        if summary["CRITICAL"] > 0:
            gate_decision = "FAIL"
            gate_reason = f"{summary['CRITICAL']} CRITICAL finding(s) must be resolved"
            verdict = "REJECTED"
        elif summary["HIGH"] > self.max_high_findings:
            gate_decision = "FAIL"
            gate_reason = (
                f"{summary['HIGH']} HIGH findings exceed threshold (max {self.max_high_findings})"
            )
            verdict = "APPROVED_WITH_CONCERNS"
        else:
            gate_decision = "PASS"
            gate_reason = (
                "No blocking critical or excessive high findings"
                if summary["HIGH"] == 0
                else f"Passed with {summary['HIGH']} HIGH finding(s) tracked for remediation"
            )
            verdict = "APPROVED" if summary["HIGH"] == 0 else "APPROVED_WITH_CONCERNS"

        return ReviewResult(
            review_id=f"review-{uuid4().hex[:8]}",
            timestamp=datetime.now(UTC),
            specification_id=resolved_spec_id or "spec-unknown",
            verdict=verdict,
            findings=all_findings,
            conflicts=resolved_conflicts,
            summary=summary,
            gate_decision=gate_decision,
            gate_reason=gate_reason,
            reviewers_executed=reviewers_executed,
            duration_seconds=duration_seconds,
            total_cost_usd=total_cost_usd,
        )

    def _detect_conflicts(
        self,
        findings: list[ReviewFinding],
    ) -> list[tuple[ReviewFinding, ReviewFinding]]:
        """Detect contradictory recommendations targeting the same requirement across domains."""
        conflicts: list[tuple[ReviewFinding, ReviewFinding]] = []
        by_requirement: dict[str, list[ReviewFinding]] = {}

        for finding in findings:
            key = finding.requirement_id or "__global__"
            by_requirement.setdefault(key, []).append(finding)

        for req_findings in by_requirement.values():
            for finding_a, finding_b in combinations(req_findings, 2):
                if finding_a.category == finding_b.category:
                    continue
                if self._are_conflicting(finding_a, finding_b):
                    conflicts.append((finding_a, finding_b))

        return conflicts

    def _are_conflicting(
        self,
        finding_a: ReviewFinding,
        finding_b: ReviewFinding,
    ) -> bool:
        """Check directional and symmetric contradiction patterns between two findings."""
        text_a = f"{finding_a.description} {finding_a.recommendation}".lower()
        text_b = f"{finding_b.description} {finding_b.recommendation}".lower()

        pair_key = (finding_a.category, finding_b.category)
        reverse_key = (finding_b.category, finding_a.category)

        if pair_key in CONFLICT_KEYWORD_PAIRS:
            for kw_a, kw_b in CONFLICT_KEYWORD_PAIRS[pair_key]:
                if kw_a in text_a and kw_b in text_b:
                    return True
        if reverse_key in CONFLICT_KEYWORD_PAIRS:
            for kw_b, kw_a in CONFLICT_KEYWORD_PAIRS[reverse_key]:
                if kw_a in text_a and kw_b in text_b:
                    return True

        # Generic fallback: explicit add vs remove / enable vs disable on same requirement
        generic_opposites = (("enable", "disable"), ("add", "remove"), ("require", "omit"))
        for pos, neg in generic_opposites:
            if (pos in text_a and neg in text_b) or (neg in text_a and pos in text_b):
                return True

        return False

    async def _resolve_conflict(
        self,
        conflict: tuple[ReviewFinding, ReviewFinding],
    ) -> ReviewConflict:
        """
        Resolve a conflict using severity rank first, then domain priority
        (Security > Architecture > Performance > Scalability > Observability > Maintainability),
        with optional LLM tiebreaker commentary when ``llm_judge`` is provided.
        """
        finding_a, finding_b = conflict

        if finding_a.severity.rank > finding_b.severity.rank:
            chosen, other = finding_a, finding_b
            rationale = (
                f"Prioritized {chosen.category.value} ({chosen.severity.value}) over "
                f"{other.category.value} ({other.severity.value}) due to higher severity"
            )
        elif finding_b.severity.rank > finding_a.severity.rank:
            chosen, other = finding_b, finding_a
            rationale = (
                f"Prioritized {chosen.category.value} ({chosen.severity.value}) over "
                f"{other.category.value} ({other.severity.value}) due to higher severity"
            )
        else:
            idx_a = (
                CATEGORY_PRIORITY.index(finding_a.category)
                if finding_a.category in CATEGORY_PRIORITY
                else len(CATEGORY_PRIORITY)
            )
            idx_b = (
                CATEGORY_PRIORITY.index(finding_b.category)
                if finding_b.category in CATEGORY_PRIORITY
                else len(CATEGORY_PRIORITY)
            )
            if idx_a <= idx_b:
                chosen, other = finding_a, finding_b
            else:
                chosen, other = finding_b, finding_a
            rationale = (
                f"{chosen.category.value.capitalize()} takes precedence over "
                f"{other.category.value} at equal severity ({chosen.severity.value})"
            )

        if self.llm_judge is not None:
            rationale = await self._llm_refine_rationale(finding_a, finding_b, chosen, rationale)

        cats_ordered = sorted(
            [finding_a.category, finding_b.category],
            key=lambda c: (
                CATEGORY_PRIORITY.index(c) if c in CATEGORY_PRIORITY else len(CATEGORY_PRIORITY)
            ),
        )
        conflict_type = f"{cats_ordered[0].value}_vs_{cats_ordered[1].value}"

        return ReviewConflict(
            finding_a=finding_a,
            finding_b=finding_b,
            conflict_type=conflict_type,
            resolution=f"Resolved in favor of {chosen.reviewer}",
            chosen_recommendation=chosen.recommendation,
            rationale=rationale,
            requirement_id=finding_a.requirement_id or finding_b.requirement_id,
        )

    async def _llm_refine_rationale(
        self,
        finding_a: ReviewFinding,
        finding_b: ReviewFinding,
        chosen: ReviewFinding,
        fallback_rationale: str,
    ) -> str:
        """Optionally ask an LLM judge to provide contextual tradeoff rationale."""
        assert self.llm_judge is not None
        prompt = (
            f"Two independent reviewers gave conflicting advice on {finding_a.requirement_id}:\n"
            f"- {finding_a.reviewer} ({finding_a.severity.value}, {finding_a.category.value}): "
            f"{finding_a.recommendation}\n"
            f"- {finding_b.reviewer} ({finding_b.severity.value}, {finding_b.category.value}): "
            f"{finding_b.recommendation}\n"
            f"Selected recommendation: {chosen.recommendation}\n"
            'Provide a concise 1-sentence rationale as JSON: {"rationale": "..."}'
        )
        try:
            resp = await self.llm_judge.invoke(
                messages=[
                    Message(role=Role.SYSTEM, content="You resolve engineering review tradeoffs."),
                    Message(role=Role.USER, content=prompt),
                ],
                temperature=0.0,
                max_tokens=150,
            )
            data = json.loads(resp.content.strip())
            if isinstance(data, dict) and data.get("rationale"):
                return str(data["rationale"])
        except Exception as exc:
            logger.debug("LLM conflict rationale fallback: %s", exc)
        return fallback_rationale
