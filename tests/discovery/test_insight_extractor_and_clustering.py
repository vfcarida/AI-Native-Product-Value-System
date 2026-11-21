"""
Unit tests for InsightExtractor, ThemeClusterer, OpportunityMap, Exporters & Dashboard (EVO-022).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from product_value_system.discovery import (
    InsightExtractor,
    InterviewInsights,
    OpportunityMap,
    PainPoint,
    PainPointSeverity,
    PIIRedactor,
    ReviewStatus,
    SynthesisDashboard,
    ThemeClusterer,
    TranscriptProcessor,
    export_clustered_themes,
    export_insights,
)
from product_value_system.discovery.theme_clusterer import _cosine_dbscan_numpy


def _make_mock_llm(captured_prompts: list[str] | None = None) -> Any:
    """Build an async mock LLM returning fenced/plain JSON for each extraction prompt."""

    async def _llm(prompt: str, temperature: float = 0.2) -> str:
        if captured_prompts is not None:
            captured_prompts.append(prompt)
        if "executive summary" in prompt.lower():
            return (
                "Customers report high friction from 10-15 second transfer waits and multi-step "
                "confirmations when sending urgent family payments."
            )
        if "extract pain points" in prompt.lower():
            return (
                "```json\n"
                + json.dumps(
                    [
                        {
                            "description": "PIX transfers are too slow (10-15 second wait)",
                            "severity": "high",
                            "quotes": [
                                "It takes forever! Sometimes 10-15 seconds just waiting."
                            ],
                            "confidence": 0.92,
                        },
                        {
                            "description": "Too many confirmation steps",
                            "severity": "low",
                            "quotes": ["Why do I need to confirm 3 times?"],
                            "confidence": 1.4,  # Should be clamped to 1.0
                        },
                    ]
                )
                + "\n```"
            )
        if "jobs-to-be-done" in prompt.lower():
            return json.dumps(
                [
                    {
                        "job": "Send money quickly to family members",
                        "situation": "Weekly support transfer",
                        "motivation": "Ensure family receives funds immediately",
                        "expected_outcome": "Instant completion under 2 seconds",
                        "current_solution": "Ask for PIX key on WhatsApp then transfer",
                        "quotes": ["I want to send money quickly to family"],
                    }
                ]
            )
        if "memorable, insightful quotes" in prompt.lower():
            return json.dumps(
                [
                    {
                        "text": "It takes forever! Sometimes 10-15 seconds just waiting.",
                        "context": "Frustration with transfer latency",
                    }
                ]
            )
        if "main themes" in prompt.lower():
            return json.dumps(["Speed", "Simplicity", "Trust"])
        if "theme name" in prompt.lower():
            return "Slow Transfer Processing"
        return (
            "Customers report high friction from 10-15 second transfer waits and multi-step "
            "confirmations when sending urgent family payments."
        )

    return _llm


@pytest.mark.unit
@pytest.mark.asyncio
async def test_insight_extractor_llm_backed_extraction_and_traceability() -> None:
    """InsightExtractor parses fenced JSON, clamps confidence, and attributes quote timestamps."""
    processor = TranscriptProcessor()
    transcript = processor.process_transcript(
        "[00:01:30] Interviewer: What is your biggest frustration?\n"
        "[00:01:35] Participant: It takes forever! Sometimes 10-15 seconds just waiting.\n"
        "[00:02:05] Participant: Why do I need to confirm 3 times?\n",
        interview_id="INT-101",
    )
    extractor = InsightExtractor(llm_callable=_make_mock_llm(), pii_redactor=PIIRedactor())
    insights = await extractor.extract_insights(transcript)

    assert insights.interview_id == "INT-101"
    assert len(insights.pain_points) == 2
    assert insights.pain_points[0].severity == PainPointSeverity.HIGH
    assert insights.pain_points[0].confidence == pytest.approx(0.92)
    assert insights.pain_points[0].segment_turns == [1]
    assert insights.pain_points[1].confidence == 1.0  # Clamped from 1.4

    assert len(insights.jobs_to_be_done) == 1
    assert insights.jobs_to_be_done[0].job == "Send money quickly to family members"

    assert len(insights.quotes) == 1
    assert insights.quotes[0].timestamp == "00:01:35"
    assert insights.quotes[0].speaker == "Participant"
    assert insights.quotes[0].turn_number == 1

    assert insights.themes == ["Speed", "Simplicity", "Trust"]
    assert "10-15 second" in insights.summary


@pytest.mark.unit
@pytest.mark.asyncio
async def test_insight_extractor_redacts_pii_before_calling_llm() -> None:
    """Raw PII is never sent in prompts passed to the LLM callable."""
    processor = TranscriptProcessor()
    transcript = processor.process_transcript(
        "[00:00:10] João: Contact me at joao@secret.br or +55 (11) 98765-4321. "
        "It takes forever! Sometimes 10-15 seconds just waiting.\n",
        interview_id="INT-PRIV",
    )
    seen_prompts: list[str] = []
    extractor = InsightExtractor(
        llm_callable=_make_mock_llm(seen_prompts),
        pii_redactor=PIIRedactor(),
    )
    insights = await extractor.extract_insights(transcript)

    assert len(seen_prompts) >= 4
    for prompt in seen_prompts:
        assert "joao@secret.br" not in prompt
        assert "98765-4321" not in prompt
        assert "João" not in prompt
    assert insights.redaction_counts["emails"] == 1
    assert insights.redaction_counts["phones"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_insight_extractor_offline_heuristic_mode() -> None:
    """Offline fallback (llm_callable=None) extracts pain points, JTBD, quotes, and summary."""
    processor = TranscriptProcessor()
    transcript = processor.process_transcript(
        "[00:00:20] Interviewer: Tell me about your transfers.\n"
        "[00:00:30] Participant: I want to send money daily, but transfers are too slow.\n"
        "[00:01:10] Participant: When an error happens, the message is unclear and confusing.\n",
        interview_id="INT-OFFLINE",
    )
    extractor = InsightExtractor(llm_callable=None, pii_redactor=PIIRedactor())
    insights = await extractor.extract_insights(transcript)

    assert len(insights.pain_points) >= 2
    assert any(p.severity == PainPointSeverity.HIGH for p in insights.pain_points)
    assert len(insights.jobs_to_be_done) >= 1
    assert len(insights.quotes) >= 2
    assert insights.quotes[0].timestamp != ""
    assert "Speed" in insights.themes
    assert "Error Handling" in insights.themes
    assert insights.summary != ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_human_review_workflow_and_json_round_trip() -> None:
    """Human review approve/reject/revision transitions persist across JSON round-trips."""
    processor = TranscriptProcessor()
    transcript = processor.process_transcript(
        "[00:00:10] Participant: Transfers are way too slow.",
        interview_id="INT-REV",
    )
    insights = await InsightExtractor().extract_insights(transcript)
    assert insights.review_status == ReviewStatus.PENDING_REVIEW

    insights.request_revision("Check severity of wait time")
    assert insights.review_status == ReviewStatus.NEEDS_REVISION

    insights.approve("Verified against audio recording")
    assert insights.review_status == ReviewStatus.APPROVED
    assert all(pp.review_status == ReviewStatus.APPROVED for pp in insights.pain_points)

    restored = InterviewInsights.from_dict(insights.to_dict())
    assert restored.interview_id == "INT-REV"
    assert restored.review_status == ReviewStatus.APPROVED
    assert restored.review_notes == "Verified against audio recording"
    assert len(restored.pain_points) == len(insights.pain_points)

    restored.reject("Duplicate interview")
    assert restored.review_status == ReviewStatus.REJECTED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_theme_clusterer_groups_paraphrased_pain_points() -> None:
    """Paraphrased slow-transfer and error pain points cluster into separate themes."""
    pain_points = [
        PainPoint(
            description="Transfers take forever when paying bills",
            severity=PainPointSeverity.HIGH,
            frequency=1,
            quotes=["It takes forever!"],
            confidence=0.9,
            interview_id="INT-001",
        ),
        PainPoint(
            description="Waiting too long for transfer confirmation",
            severity=PainPointSeverity.HIGH,
            frequency=1,
            quotes=["Always waiting 15 seconds"],
            confidence=0.88,
            interview_id="INT-002",
        ),
        PainPoint(
            description="Need faster transfer processing speed",
            severity=PainPointSeverity.CRITICAL,
            frequency=1,
            quotes=["Need faster PIX settlement"],
            confidence=0.95,
            interview_id="INT-003",
        ),
        PainPoint(
            description="Error messages are unclear when a transfer fails",
            severity=PainPointSeverity.MEDIUM,
            frequency=1,
            quotes=["I never know what went wrong"],
            confidence=0.82,
            interview_id="INT-001",
        ),
        PainPoint(
            description="Confusing failure message with no clear next steps",
            severity=PainPointSeverity.MEDIUM,
            frequency=1,
            quotes=["Just says error with no help"],
            confidence=0.8,
            interview_id="INT-004",
        ),
    ]

    clusterer = ThemeClusterer()
    themes = await clusterer.cluster_pain_points(
        pain_points,
        similarity_threshold=0.7,
        total_interviews=4,
    )

    assert len(themes) == 2
    assert themes[0].theme_name == "Transfer Speed"
    assert themes[0].frequency == 3
    assert themes[0].prevalence_pct == 75.0
    assert themes[0].highest_severity == PainPointSeverity.CRITICAL

    assert themes[1].theme_name == "Error Handling"
    assert themes[1].frequency == 2
    assert themes[1].prevalence_pct == 50.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_theme_clusterer_custom_async_embeddings_and_llm_naming() -> None:
    """ThemeClusterer supports custom async embedding callables and LLM theme naming."""

    async def _custom_embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "speed" in t.lower() else [0.0, 1.0] for t in texts]

    points = [
        PainPoint("Speed issue A", PainPointSeverity.HIGH, 1, ["Slow A"], 0.9, "INT-1"),
        PainPoint("Speed issue B", PainPointSeverity.HIGH, 1, ["Slow B"], 0.85, "INT-2"),
        PainPoint("Unique outlier", PainPointSeverity.LOW, 1, ["Minor"], 0.6, "INT-3"),
    ]
    clusterer = ThemeClusterer(
        embedding_model=_custom_embed,
        llm_callable=_make_mock_llm(),
    )
    themes = await clusterer.cluster_pain_points(
        points,
        similarity_threshold=0.8,
        include_unclustered=False,
    )
    assert len(themes) == 1
    assert themes[0].theme_name == "Slow Transfer Processing"
    assert themes[0].frequency == 2

    with_noise = await clusterer.cluster_pain_points(
        points,
        similarity_threshold=0.8,
        include_unclustered=True,
    )
    assert len(with_noise) == 2


@pytest.mark.unit
def test_cosine_dbscan_numpy_fallback() -> None:
    """Pure-numpy cosine DBSCAN groups identical vectors and marks orthogonal vectors as noise."""
    vectors = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.98, 0.2, 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    labels = _cosine_dbscan_numpy(vectors, eps=0.2, min_samples=2)
    assert labels[0] == labels[1]
    assert labels[0] >= 0
    assert labels[2] == -1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_opportunity_map_populate_merge_prioritize_and_persist(tmp_path: Path) -> None:
    """OpportunityMap converts clustered themes into prioritized opportunities and saves/loads."""
    points = [
        PainPoint("Slow transfers", PainPointSeverity.HIGH, 1, ["Takes forever"], 0.9, "INT-1"),
        PainPoint("Waiting too long", PainPointSeverity.CRITICAL, 1, ["Too slow"], 0.95, "INT-2"),
    ]
    themes = await ThemeClusterer().cluster_pain_points(points, similarity_threshold=0.55)

    omap = OpportunityMap(desired_outcome="Cut PIX checkout latency by 50%")
    added = omap.populate_from_themes(themes)
    assert len(added) == 1

    # Merging an opportunity with the same name updates evidence & quotes without duplicating
    omap.add_opportunity(
        name="Transfer Speed",
        description="Updated evidence",
        evidence=["Extra latency report"],
        customer_quotes=["Still slow at peak hours"],
        interview_ids=["INT-3"],
        severity=PainPointSeverity.CRITICAL,
    )
    assert len(omap.opportunities) == 1
    assert omap.opportunities[0].frequency == 3
    assert len(omap.opportunities[0].customer_quotes) == 2

    ctx = omap.to_problem_context()
    assert ctx["opportunity_count"] == 1
    assert ctx["top_opportunities"][0]["name"] == "Transfer Speed"
    assert "# Opportunity Map" in omap.to_markdown()

    saved = omap.save(tmp_path / "opportunities.json")
    loaded = OpportunityMap.load(saved)
    assert loaded.desired_outcome == omap.desired_outcome
    assert len(loaded.opportunities) == 1
    assert loaded.opportunities[0].name == "Transfer Speed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exporters_json_markdown_notion_and_invalid_format() -> None:
    """Insights export cleanly to JSON, Markdown, and Notion formats."""
    processor = TranscriptProcessor()
    transcript = processor.process_transcript(
        "[00:01:35] Participant: It takes forever! Sometimes 10-15 seconds just waiting.\n",
        interview_id="INT-EXP",
    )
    insights = await InsightExtractor(llm_callable=_make_mock_llm()).extract_insights(transcript)

    json_out = export_insights(insights, fmt="json")
    assert json.loads(json_out)["interview_id"] == "INT-EXP"

    md_out = export_insights(insights, fmt="markdown")
    assert "# Interview Synthesis: INT-EXP" in md_out
    assert "## Pain Points" in md_out
    assert "## Jobs-to-be-Done" in md_out

    notion_out = export_insights(insights, fmt="notion")
    assert "| Property | Value |" in notion_out
    assert "| Severity | Pain Point | Confidence | Evidence Quote |" in notion_out
    assert "## 💬 Traceable Quotes" in notion_out

    with pytest.raises(ValueError, match="Unsupported export format"):
        export_insights(insights, fmt="xml")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cluster_report_export_and_dashboard_snapshot() -> None:
    """Cluster report exporter and SynthesisDashboard produce trending themes & time series."""
    i1 = InterviewInsights(
        interview_id="INT-1",
        pain_points=[PainPoint("Slow", PainPointSeverity.HIGH, 1, ["Slow"], 0.9, "INT-1")],
        jobs_to_be_done=[],
        quotes=[],
        themes=["Speed", "Trust"],
        summary="Summary 1",
        redaction_counts={"emails": 1},
        created_at="2026-11-18T10:00:00+00:00",
    )
    i2 = InterviewInsights(
        interview_id="INT-2",
        pain_points=[
            PainPoint("Waiting too long", PainPointSeverity.CRITICAL, 1, ["Wait"], 0.92, "INT-2")
        ],
        jobs_to_be_done=[],
        quotes=[],
        themes=["Speed", "Error Handling"],
        summary="Summary 2",
        redaction_counts={"phones": 2},
        created_at="2026-11-19T12:00:00+00:00",
    )
    i2.approve("Looks great")

    themes = await ThemeClusterer().cluster_pain_points(
        i1.pain_points + i2.pain_points,
        similarity_threshold=0.5,
        total_interviews=2,
    )
    report_json = json.loads(export_clustered_themes(themes, total_interviews=2, fmt="json"))
    assert report_json["total_interviews"] == 2
    assert report_json["cluster_count"] == 1
    assert "Clustered Themes Across 2 Interviews" in export_clustered_themes(
        themes, total_interviews=2, fmt="markdown"
    )

    snapshot = SynthesisDashboard().build_snapshot([i1, i2])
    assert snapshot.total_interviews == 2
    assert snapshot.total_pain_points == 2
    assert snapshot.total_pii_redacted == 3
    assert snapshot.severity_distribution["critical"] == 1
    assert snapshot.severity_distribution["high"] == 1
    assert snapshot.trending_themes[0]["theme"] == "Speed"
    assert snapshot.trending_themes[0]["interview_count"] == 2
    assert len(snapshot.insights_over_time) == 2
    assert snapshot.review_status_counts["approved"] == 1
    assert snapshot.review_status_counts["pending_review"] == 1
    assert "# Interview Synthesis Dashboard" in snapshot.to_markdown()
