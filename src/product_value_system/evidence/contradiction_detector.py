"""
Cross-source contradiction detector combining semantic similarity with LLM or
deterministic offline conflict analysis (EVO-023).
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np

from product_value_system.discovery.theme_clusterer import default_semantic_embeddings
from product_value_system.evidence.models import Contradiction, Evidence

logger = logging.getLogger(__name__)

LLMCallable = Callable[..., Awaitable[str] | str]
EmbeddingCallable = Callable[[list[str]], Awaitable[list[list[float]]] | list[list[float]]]

_POSITIVE_DEMAND_RE = re.compile(
    r"\b(must[- ]have|want|wants|love|high demand|essential|critical|important|"
    r"prefer|fast|instant|high adoption|reliable|works well|popular)\b",
    re.I,
)
_NEGATIVE_OR_LOW_RE = re.compile(
    r"\b(not important|low|rarely|never|complaints?|fail\w*|broken|frustrat\w*|"
    r"abandon\w*|disable\w*|slow|takes forever|waiting|unnecessary|no demand|reject\w*)\b",
    re.I,
)
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _extract_json(raw: str) -> Any:
    cleaned = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    return json.loads(cleaned)


class ContradictionDetector:
    """Detect semantic, quantitative, and temporal contradictions across evidence sources."""

    def __init__(
        self,
        llm_callable: LLMCallable | None = None,
        embedding_model: EmbeddingCallable | None = None,
    ) -> None:
        self.llm = llm_callable
        self.embedding_model: EmbeddingCallable = embedding_model or default_semantic_embeddings

    async def detect_contradictions(
        self,
        evidence_list: list[Evidence],
        similarity_threshold: float = 0.7,
    ) -> list[Contradiction]:
        """Compare semantically related evidence pairs and return detected contradictions."""
        if len(evidence_list) < 2:
            return []

        claims = [e.claim for e in evidence_list]
        embeddings = await self._get_embeddings(claims)

        contradictions: list[Contradiction] = []
        for i in range(len(evidence_list)):
            for j in range(i + 1, len(evidence_list)):
                sim = self._cosine_similarity(embeddings[i], embeddings[j])
                if sim >= similarity_threshold:
                    contradiction = await self._check_contradiction(
                        evidence_list[i],
                        evidence_list[j],
                    )
                    if contradiction is not None:
                        contradictions.append(contradiction)

        contradictions.sort(key=lambda c: c.severity, reverse=True)
        return contradictions

    async def _get_embeddings(self, texts: list[str]) -> np.ndarray:
        result = self.embedding_model(texts)
        resolved = await result if inspect.isawaitable(result) else result
        return np.asarray(resolved, dtype=float)

    @staticmethod
    def _cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
        norm1 = float(np.linalg.norm(vec1))
        norm2 = float(np.linalg.norm(vec2))
        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0
        return float(np.clip(np.dot(vec1, vec2) / (norm1 * norm2), -1.0, 1.0))

    async def _check_contradiction(
        self,
        evidence1: Evidence,
        evidence2: Evidence,
    ) -> Contradiction | None:
        if self.llm is None:
            return self._heuristic_check_contradiction(evidence1, evidence2)

        prompt = f"""Analyze these two pieces of evidence for contradictions.

Evidence 1 ({evidence1.type.value}, {evidence1.quality.value}):
Claim: {evidence1.claim}
Source: {evidence1.source}
Sample size: {evidence1.sample_size}
Date: {evidence1.date.isoformat()}
Supporting data: {evidence1.supporting_data}

Evidence 2 ({evidence2.type.value}, {evidence2.quality.value}):
Claim: {evidence2.claim}
Source: {evidence2.source}
Sample size: {evidence2.sample_size}
Date: {evidence2.date.isoformat()}
Supporting data: {evidence2.supporting_data}

Do these pieces of evidence contradict each other?

Output JSON:
{{
  "contradicts": true/false,
  "type": "semantic" | "quantitative" | "temporal",
  "severity": 0.0-1.0,
  "explanation": "Why they contradict or don't",
  "suggested_research": "Optional follow-up research to resolve the conflict"
}}
"""
        try:
            raw = self.llm(prompt, temperature=0.1)
            response = str(await raw if inspect.isawaitable(raw) else raw)
            result = _extract_json(response)
            if result.get("contradicts"):
                severity = float(result.get("severity", 0.75))
                return Contradiction(
                    evidence1=evidence1,
                    evidence2=evidence2,
                    contradiction_type=str(result.get("type", "semantic")),
                    severity=severity,
                    explanation=str(result.get("explanation", "Conflicting claims detected.")),
                    requires_human_review=severity >= 0.7,
                    suggested_research=str(
                        result.get("suggested_research")
                        or "Conduct targeted validation study across representative user segments."
                    ),
                )
        except Exception as exc:
            logger.warning(
                "Contradiction check failed for %s vs %s: %s",
                evidence1.id,
                evidence2.id,
                exc,
            )
        return None

    def _heuristic_check_contradiction(
        self,
        evidence1: Evidence,
        evidence2: Evidence,
    ) -> Contradiction | None:
        """Deterministic offline detector for quantitative, semantic, and temporal conflicts."""
        c1, c2 = evidence1.claim, evidence2.claim

        # 1. Quantitative conflict: explicit percentage mismatch or demand vs low adoption (<25%)
        pct1 = _extract_percentage(evidence1)
        pct2 = _extract_percentage(evidence2)

        if pct1 is not None and pct2 is not None and abs(pct1 - pct2) >= 25.0:
            d1 = evidence1.date.replace(tzinfo=None)
            d2 = evidence2.date.replace(tzinfo=None)
            days_apart = abs((d1 - d2).days)
            c_type = "temporal" if days_apart >= 90 else "quantitative"
            severity = min(1.0, 0.55 + abs(pct1 - pct2) / 100.0)
            return Contradiction(
                evidence1=evidence1,
                evidence2=evidence2,
                contradiction_type=c_type,
                severity=severity,
                explanation=(
                    f"Quantitative discrepancy ({pct1:.1f}% vs {pct2:.1f}%) between "
                    f"{evidence1.type.value} and {evidence2.type.value}."
                ),
                requires_human_review=severity >= 0.7,
                suggested_research=(
                    "Reconcile metric definitions, time windows, and user cohorts across sources."
                ),
            )

        # Positive demand/preference vs low quantitative adoption (<25%)
        for claim_ev, metric_ev, metric_pct in (
            (evidence1, evidence2, pct2),
            (evidence2, evidence1, pct1),
        ):
            if (
                metric_pct is not None
                and metric_pct <= 25.0
                and _POSITIVE_DEMAND_RE.search(claim_ev.claim)
                and not _NEGATIVE_OR_LOW_RE.search(claim_ev.claim)
            ):
                return Contradiction(
                    evidence1=claim_ev,
                    evidence2=metric_ev,
                    contradiction_type="quantitative",
                    severity=0.85,
                    explanation=(
                        f"{claim_ev.type.value} states '{claim_ev.claim}' while "
                        f"{metric_ev.type.value} shows only {metric_pct:.1f}% adoption/usage."
                    ),
                    requires_human_review=True,
                    suggested_research=(
                        "Run a broader quantitative survey and segmented funnel analysis "
                        "to determine whether qualitative demand generalizes."
                    ),
                )

        # 2. Semantic polarity conflict across sources on the same topic
        pos1 = bool(_POSITIVE_DEMAND_RE.search(c1))
        neg1 = bool(_NEGATIVE_OR_LOW_RE.search(c1))
        pos2 = bool(_POSITIVE_DEMAND_RE.search(c2))
        neg2 = bool(_NEGATIVE_OR_LOW_RE.search(c2))

        # Pure positive vs negative (or "not important" negation vs "important/want/must-have")
        c1_negated = (
            "not important" in c1.lower() or "no demand" in c1.lower() or (neg1 and not pos1)
        )
        c2_negated = (
            "not important" in c2.lower() or "no demand" in c2.lower() or (neg2 and not pos2)
        )
        c1_positive = pos1 and not c1_negated
        c2_positive = pos2 and not c2_negated

        if (c1_positive and c2_negated) or (c2_positive and c1_negated):
            severity = 0.78 if evidence1.type != evidence2.type else 0.65
            return Contradiction(
                evidence1=evidence1,
                evidence2=evidence2,
                contradiction_type="semantic",
                severity=severity,
                explanation=(
                    f"Conflicting conclusions between {evidence1.type.value} ('{c1}') "
                    f"and {evidence2.type.value} ('{c2}')."
                ),
                requires_human_review=severity >= 0.7,
                suggested_research=(
                    "Review underlying user segments and run targeted validation tests "
                    "to resolve disagreement."
                ),
            )

        return None


def _extract_percentage(evidence: Evidence) -> float | None:
    match = _PERCENT_RE.search(evidence.claim)
    if match:
        return float(match.group(1))
    val = evidence.supporting_data.get("value")
    if isinstance(val, (int, float)) and 0.0 <= float(val) <= 1.0:
        if "rate" in evidence.id.lower() or "adoption" in evidence.claim.lower():
            return float(val) * 100.0
    return None
