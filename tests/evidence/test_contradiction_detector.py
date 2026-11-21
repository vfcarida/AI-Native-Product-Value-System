"""
Unit tests for ContradictionDetector (LLM-backed & offline heuristic conflict detection) (EVO-023).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from product_value_system.evidence import (
    ContradictionDetector,
    Evidence,
    EvidenceQuality,
    EvidenceType,
)


def _mk(
    ev_id: str,
    claim: str,
    *,
    ev_type: EvidenceType = EvidenceType.INTERVIEW,
    quality: EvidenceQuality = EvidenceQuality.PRIMARY,
    days_old: int = 0,
    supporting_data: dict[str, object] | None = None,
) -> Evidence:
    return Evidence(
        id=ev_id,
        type=ev_type,
        quality=quality,
        source=f"src-{ev_id}",
        date=datetime.now(UTC) - timedelta(days=days_old),
        claim=claim,
        supporting_data=dict(supporting_data or {}),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_backed_contradiction_detection_parses_fenced_json() -> None:
    """ContradictionDetector invokes LLM on similar pairs and parses fenced JSON response."""

    async def _embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    async def _llm(prompt: str, temperature: float = 0.1) -> str:
        return (
            "```json\n"
            + json.dumps(
                {
                    "contradicts": True,
                    "type": "quantitative",
                    "severity": 0.9,
                    "explanation": "Interview claims must-have while analytics show 2% adoption.",
                    "suggested_research": "Run segmented survey on Android vs iOS users.",
                }
            )
            + "\n```"
        )

    ev1 = _mk("e1", "Biometric transfer is a must-have feature")
    ev2 = _mk(
        "e2",
        "Biometric adoption rate is 2.0%",
        ev_type=EvidenceType.ANALYTICS,
        supporting_data={"value": 0.02},
    )

    detector = ContradictionDetector(llm_callable=_llm, embedding_model=_embed)
    contradictions = await detector.detect_contradictions([ev1, ev2], similarity_threshold=0.7)

    assert len(contradictions) == 1
    assert contradictions[0].contradiction_type == "quantitative"
    assert contradictions[0].severity == pytest.approx(0.9)
    assert contradictions[0].requires_human_review is True
    assert "Android" in contradictions[0].suggested_research


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_backed_no_contradiction_returns_empty() -> None:
    """Agreeing similar claims yield zero contradictions when LLM reports contradicts=false."""

    async def _embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    async def _llm(prompt: str, temperature: float = 0.1) -> str:
        return json.dumps(
            {
                "contradicts": False,
                "type": "semantic",
                "severity": 0.0,
                "explanation": "Both sources agree transfers are slow.",
            }
        )

    ev1 = _mk("e1", "Transfers are too slow")
    ev2 = _mk("e2", "Average transfer time is 12 seconds", ev_type=EvidenceType.ANALYTICS)

    detector = ContradictionDetector(llm_callable=_llm, embedding_model=_embed)
    assert await detector.detect_contradictions([ev1, ev2]) == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dissimilar_claims_are_not_sent_to_llm() -> None:
    """Pairs below similarity_threshold skip LLM contradiction analysis entirely."""
    llm_calls = 0

    async def _embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0], [0.0, 1.0]]

    async def _llm(prompt: str, temperature: float = 0.1) -> str:
        nonlocal llm_calls
        llm_calls += 1
        return json.dumps(
            {"contradicts": True, "type": "semantic", "severity": 0.9, "explanation": "x"}
        )

    ev1 = _mk("e1", "Biometric adoption is 2%")
    ev2 = _mk("e2", "Pricing fees are confusing")

    detector = ContradictionDetector(llm_callable=_llm, embedding_model=_embed)
    result = await detector.detect_contradictions([ev1, ev2], similarity_threshold=0.7)
    assert result == []
    assert llm_calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_offline_heuristic_detects_biometric_demand_vs_low_adoption() -> None:
    """Offline detector catches positive interview demand vs 2% analytics adoption."""
    ev_int = _mk("int1", "Biometric authentication is a must-have feature for transfers")
    ev_ana = _mk(
        "ana1",
        "Biometric adoption rate is 2.0%",
        ev_type=EvidenceType.ANALYTICS,
        supporting_data={"value": 0.02},
    )

    detector = ContradictionDetector(llm_callable=None)
    contradictions = await detector.detect_contradictions(
        [ev_int, ev_ana], similarity_threshold=0.6
    )

    assert len(contradictions) == 1
    assert contradictions[0].contradiction_type == "quantitative"
    assert contradictions[0].severity >= 0.8
    assert contradictions[0].requires_human_review is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_offline_heuristic_detects_semantic_polarity_conflict() -> None:
    """Offline detector flags opposing conclusions across sources on the same topic."""
    ev_int = _mk("int1", "Users want faster transfer speed immediately")
    ev_res = _mk(
        "res1",
        "Transfer speed is not important to casual banking users",
        ev_type=EvidenceType.COMPETITOR_RESEARCH,
        quality=EvidenceQuality.SECONDARY,
    )

    detector = ContradictionDetector(llm_callable=None)
    contradictions = await detector.detect_contradictions(
        [ev_int, ev_res], similarity_threshold=0.6
    )

    assert len(contradictions) == 1
    assert contradictions[0].contradiction_type == "semantic"
    assert contradictions[0].requires_human_review is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_offline_heuristic_detects_temporal_percentage_divergence() -> None:
    """Divergent percentage metrics separated by >90 days classify as temporal contradiction."""
    ev_old = _mk(
        "old",
        "Biometric adoption rate is 52.0%",
        ev_type=EvidenceType.ANALYTICS,
        days_old=200,
    )
    ev_new = _mk(
        "new",
        "Biometric adoption rate is 12.0%",
        ev_type=EvidenceType.ANALYTICS,
        days_old=2,
    )

    detector = ContradictionDetector(llm_callable=None)
    contradictions = await detector.detect_contradictions(
        [ev_old, ev_new], similarity_threshold=0.6
    )

    assert len(contradictions) == 1
    assert contradictions[0].contradiction_type == "temporal"
    assert contradictions[0].severity >= 0.7


@pytest.mark.unit
@pytest.mark.asyncio
async def test_offline_heuristic_agrees_when_both_sources_negative() -> None:
    """Two sources both reporting biometric failures do NOT trigger a false contradiction."""
    ev1 = _mk("t1", "Users report 200 support complaints about biometric failures")
    ev2 = _mk("i1", "Biometric verification fails often and frustrates users")

    detector = ContradictionDetector(llm_callable=None)
    contradictions = await detector.detect_contradictions([ev1, ev2], similarity_threshold=0.6)
    assert contradictions == []


@pytest.mark.unit
def test_cosine_similarity_handles_zero_norm_vectors() -> None:
    """_cosine_similarity safely returns 0.0 for zero vectors instead of NaN."""
    zero = np.array([0.0, 0.0, 0.0])
    unit = np.array([1.0, 0.0, 0.0])
    assert ContradictionDetector._cosine_similarity(zero, unit) == 0.0
    assert ContradictionDetector._cosine_similarity(unit, unit) == pytest.approx(1.0)
