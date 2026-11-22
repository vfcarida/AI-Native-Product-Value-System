"""
Multi-source Evidence Synthesis Engine: groups related evidence, weights sources by
quality, resolves supporting vs. contradicting sides, computes agreement & confidence,
identifies research gaps, and builds the claim-to-source provenance network (EVO-023).
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from collections import defaultdict
from datetime import UTC, datetime

from product_value_system.evidence.contradiction_detector import (
    ContradictionDetector,
    LLMCallable,
)
from product_value_system.evidence.models import (
    Claim,
    Contradiction,
    Evidence,
    EvidenceType,
    SynthesisReport,
)
from product_value_system.evidence.quality_scorer import QualityScorer

logger = logging.getLogger(__name__)


def _parse_json_list(raw: str) -> list[str]:
    cleaned = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    parsed = json.loads(cleaned)
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


class SynthesisEngine:
    """Synthesize multi-source evidence into weighted, provenance-tracked claims."""

    def __init__(
        self,
        contradiction_detector: ContradictionDetector | None = None,
        quality_scorer: QualityScorer | None = None,
        llm_callable: LLMCallable | None = None,
    ) -> None:
        self.contradiction_detector = contradiction_detector or ContradictionDetector(
            llm_callable=llm_callable
        )
        self.quality_scorer = quality_scorer or QualityScorer()
        self.llm = llm_callable

    async def synthesize(
        self,
        topic: str,
        evidence_list: list[Evidence],
        *,
        similarity_threshold: float = 0.7,
        grouping_threshold: float = 0.55,
    ) -> SynthesisReport:
        """Synthesize heterogeneous evidence into a complete SynthesisReport."""
        if not evidence_list:
            return SynthesisReport(
                topic=topic,
                date=datetime.now(UTC),
                claims=[],
                contradictions=[],
                evidence_gaps=[
                    f"No evidence collected yet for '{topic}'. Gather interviews, "
                    "support tickets, and product analytics."
                ],
                recommendations=[
                    "Collect primary evidence from at least 2 independent source types "
                    "before making product decisions."
                ],
                evidence_network={},
                metadata={"evidence_count": 0},
            )

        # 1. Detect pairwise contradictions across sources
        contradictions = await self.contradiction_detector.detect_contradictions(
            evidence_list,
            similarity_threshold=similarity_threshold,
        )

        # 2. Score each evidence item's quality (0-1)
        evidence_scores = {e.id: self.quality_scorer.score(e) for e in evidence_list}

        # 3. Group semantically related claims for triangulation
        claim_groups = await self._group_similar_claims(
            evidence_list,
            grouping_threshold=grouping_threshold,
        )

        # 4. Synthesize weighted claims with supporting/contradicting split & provenance
        claims: list[Claim] = []
        for group in claim_groups:
            claims.append(await self._synthesize_claim(group, evidence_scores, contradictions))

        claims.sort(
            key=lambda c: (
                c.overall_confidence,
                len(c.supporting_evidence) + len(c.contradicting_evidence),
            ),
            reverse=True,
        )

        # 5. Identify evidence gaps & actionable recommendations
        gaps = await self._identify_gaps(topic, claims, evidence_list)
        contradiction_dicts = [c.to_dict() for c in contradictions]
        recommendations = await self._generate_recommendations(
            claims, contradiction_dicts, gaps
        )

        # 6. Build claim -> source provenance network
        evidence_network = self._build_evidence_network(claims)

        return SynthesisReport(
            topic=topic,
            date=datetime.now(UTC),
            claims=claims,
            contradictions=contradiction_dicts,
            evidence_gaps=gaps,
            recommendations=recommendations,
            evidence_network=evidence_network,
            metadata={
                "evidence_count": len(evidence_list),
                "source_types": sorted({e.type.value for e in evidence_list}),
                "high_severity_contradictions": sum(
                    1 for c in contradictions if c.requires_human_review
                ),
            },
        )

    async def _group_similar_claims(
        self,
        evidence_list: list[Evidence],
        *,
        grouping_threshold: float = 0.55,
    ) -> list[list[Evidence]]:
        """Group related evidence with average-linkage clustering (avoids single-link chaining)."""
        if len(evidence_list) <= 1:
            return [evidence_list] if evidence_list else []

        embeddings = await self.contradiction_detector._get_embeddings(
            [e.claim for e in evidence_list]
        )
        groups: list[list[int]] = []

        for idx in range(len(evidence_list)):
            best_group_idx: int | None = None
            best_avg_sim = grouping_threshold

            for g_idx, member_indices in enumerate(groups):
                sims = [
                    self.contradiction_detector._cosine_similarity(embeddings[idx], embeddings[m])
                    for m in member_indices
                ]
                avg_sim = sum(sims) / len(sims)
                if avg_sim >= best_avg_sim:
                    best_avg_sim = avg_sim
                    best_group_idx = g_idx

            if best_group_idx is not None:
                groups[best_group_idx].append(idx)
            else:
                groups.append([idx])

        return [[evidence_list[i] for i in group] for group in groups]

    async def _synthesize_claim(
        self,
        evidence_group: list[Evidence],
        evidence_scores: dict[str, float],
        contradictions: list[Contradiction],
    ) -> Claim:
        """Synthesize a weighted claim, splitting supporting vs. contradicting evidence."""
        group_ids = {e.id for e in evidence_group}
        group_contradictions = [
            c
            for c in contradictions
            if c.evidence1.id in group_ids and c.evidence2.id in group_ids
        ]

        if group_contradictions:
            supporting, contradicting = self._partition_by_weight(
                evidence_group, evidence_scores, group_contradictions
            )
        else:
            supporting = list(evidence_group)
            contradicting = []

        supporting.sort(key=lambda e: evidence_scores.get(e.id, 0.0), reverse=True)
        contradicting.sort(key=lambda e: evidence_scores.get(e.id, 0.0), reverse=True)

        total_weight = sum(evidence_scores.get(e.id, 0.0) for e in evidence_group)
        supporting_weight = sum(evidence_scores.get(e.id, 0.0) for e in supporting)
        agreement_score = (
            supporting_weight / total_weight if total_weight > 0 else 1.0
        ) if contradicting else 1.0

        mean_support_quality = (
            supporting_weight / len(supporting) if supporting else 0.0
        )
        # Multi-source diversity boost (up to +0.10) tempered by disagreement penalty
        unique_types = len({e.type for e in supporting})
        diversity_bonus = min(0.10, max(0, unique_types - 1) * 0.05)
        overall_confidence = max(
            0.0,
            min(1.0, (mean_support_quality + diversity_bonus) * (0.5 + 0.5 * agreement_score)),
        )

        statement = await self._generate_statement(supporting, contradicting)
        provenance = self._generate_provenance(supporting, contradicting)
        group_score_map = {e.id: evidence_scores.get(e.id, 0.0) for e in evidence_group}

        return Claim(
            statement=statement,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            agreement_score=round(agreement_score, 4),
            overall_confidence=round(overall_confidence, 4),
            provenance=provenance,
            quality_scores=group_score_map,
        )

    @staticmethod
    def _partition_by_weight(
        group: list[Evidence],
        scores: dict[str, float],
        contradictions: list[Contradiction],
    ) -> tuple[list[Evidence], list[Evidence]]:
        """Partition conflicting evidence so the higher-quality-weighted side leads."""
        conflict_adj: dict[str, set[str]] = defaultdict(set)
        for c in contradictions:
            conflict_adj[c.evidence1.id].add(c.evidence2.id)
            conflict_adj[c.evidence2.id].add(c.evidence1.id)

        # Greedy highest-weight-first independent set forms the primary supporting side
        ordered = sorted(group, key=lambda e: scores.get(e.id, 0.0), reverse=True)
        supporting: list[Evidence] = []
        supporting_ids: set[str] = set()
        contradicting: list[Evidence] = []

        for item in ordered:
            if any(nb in supporting_ids for nb in conflict_adj.get(item.id, set())):
                contradicting.append(item)
            else:
                supporting.append(item)
                supporting_ids.add(item.id)

        return supporting, contradicting

    async def _generate_statement(
        self,
        supporting: list[Evidence],
        contradicting: list[Evidence],
    ) -> str:
        if self.llm is not None:
            prompt = (
                "Synthesize a single clear statement (1-2 sentences) from this evidence:\n\n"
                f"Supporting Evidence:\n{[e.claim for e in supporting]}\n\n"
                f"Contradicting Evidence:\n"
                f"{[e.claim for e in contradicting] if contradicting else 'None'}\n\n"
                "Output just the statement, nothing else."
            )
            raw = self.llm(prompt, temperature=0.3)
            text = str(await raw if inspect.isawaitable(raw) else raw).strip()
            if text:
                return text

        if not supporting:
            return "Insufficient supporting evidence."

        lead = supporting[0].claim.rstrip(".")
        if len(supporting) > 1 and not contradicting:
            extra_types = sorted({e.type.value for e in supporting[1:]})
            return f"{lead} (corroborated across {', '.join(extra_types)})."
        if contradicting:
            counter = contradicting[0].claim.rstrip(".")
            return (
                f"{lead}; however, conflicting {contradicting[0].type.value} evidence "
                f"reports: {counter}."
            )
        return f"{lead}."

    @staticmethod
    def _generate_provenance(
        supporting: list[Evidence],
        contradicting: list[Evidence] | None = None,
    ) -> str:
        def _summarize(items: list[Evidence]) -> str:
            by_type: dict[str, list[Evidence]] = defaultdict(list)
            for ev in items:
                by_type[ev.type.value].append(ev)
            parts: list[str] = []
            for ev_type, evs in sorted(by_type.items()):
                total_n = sum(e.sample_size for e in evs if e.sample_size is not None)
                if total_n > 0:
                    parts.append(f"{len(evs)} {ev_type}(s) ({total_n:,} samples)")
                else:
                    parts.append(f"{len(evs)} {ev_type}(s)")
            return ", ".join(parts)

        base = _summarize(supporting)
        if contradicting:
            return f"Supporting: {base} | Contradicting: {_summarize(contradicting)}"
        return base

    async def _identify_gaps(
        self,
        topic: str,
        claims: list[Claim],
        evidence_list: list[Evidence],
    ) -> list[str]:
        if self.llm is not None:
            prompt = (
                f"Analyze this evidence synthesis for '{topic}' and identify missing evidence.\n\n"
                f"Claims:\n{[c.statement for c in claims]}\n\n"
                'Output JSON array of gap descriptions: ["gap1", "gap2"]'
            )
            try:
                raw = self.llm(prompt, temperature=0.3)
                response = str(await raw if inspect.isawaitable(raw) else raw)
                parsed = _parse_json_list(response)
                if parsed:
                    return parsed
            except Exception as exc:
                logger.debug("LLM evidence gap synthesis fallback to deterministic rules: %s", exc)

        # Deterministic coverage gap detection
        present_types = {e.type for e in evidence_list}
        gaps: list[str] = []
        if EvidenceType.ANALYTICS not in present_types:
            gaps.append(f"No quantitative product analytics evidence for '{topic}'.")
        if EvidenceType.INTERVIEW not in present_types:
            gaps.append(f"No qualitative customer interview evidence for '{topic}'.")
        if EvidenceType.SUPPORT_TICKET not in present_types:
            gaps.append(f"No support ticket complaint analysis linked to '{topic}'.")
        if EvidenceType.COMPETITOR_RESEARCH not in present_types:
            gaps.append(f"No competitive benchmarking research included for '{topic}'.")

        total_samples = sum(e.sample_size or 0 for e in evidence_list)
        if total_samples < 30:
            gaps.append(
                f"Small aggregate sample size ({total_samples}); gather larger cohort validation."
            )
        if not gaps:
            gaps.append(
                f"Validate device/segment breakdown and longitudinal stability for '{topic}'."
            )
        return gaps

    async def _generate_recommendations(
        self,
        claims: list[Claim],
        contradictions: list[dict[str, object]],
        gaps: list[str],
    ) -> list[str]:
        recommendations: list[str] = []

        for contradiction in contradictions:
            severity = float(str(contradiction.get("severity", 0.0)))
            if severity >= 0.7:
                explanation = str(contradiction.get("explanation", ""))
                suggested = str(contradiction.get("suggested_research", "")).strip()
                rec = f"Resolve high-severity contradiction (human review required): {explanation}"
                if suggested:
                    rec = f"{rec} — Suggested action: {suggested}"
                recommendations.append(rec)

        for gap in gaps[:3]:
            recommendations.append(f"Additional research needed: {gap}")

        low_confidence = [c for c in claims if c.overall_confidence < 0.5]
        if low_confidence:
            recommendations.append(
                f"Validate {len(low_confidence)} low-confidence claim(s) with primary evidence."
            )

        if not recommendations:
            recommendations.append(
                "Evidence consensus is strong; proceed to hypothesis validation or experiments."
            )
        return recommendations

    @staticmethod
    def _build_evidence_network(claims: list[Claim]) -> dict[str, list[str]]:
        network: dict[str, list[str]] = {}
        for idx, claim in enumerate(claims, start=1):
            label = f"C{idx}: {claim.statement[:60].rstrip('.')}"
            sources: list[str] = []
            for ev in claim.supporting_evidence:
                tag = f"[SUPPORT:{ev.type.value}] {ev.source}"
                if tag not in sources:
                    sources.append(tag)
            for ev in claim.contradicting_evidence:
                tag = f"[CONTRADICT:{ev.type.value}] {ev.source}"
                if tag not in sources:
                    sources.append(tag)
            network[label] = sources
        return network
