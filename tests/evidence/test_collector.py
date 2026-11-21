"""
Unit tests for EvidenceCollector across interviews, tickets, analytics, research & URLs (EVO-023).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from product_value_system.discovery import (
    InterviewInsights,
    JobToBeDone,
    PainPoint,
    PainPointSeverity,
)
from product_value_system.evidence import (
    EvidenceCollector,
    EvidenceQuality,
    EvidenceType,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.mark.unit
def test_collect_from_interviews_json_with_and_without_jtbd() -> None:
    """collect_from_interviews parses EVO-022 synthesis JSON pain points and optional JTBD."""
    collector = EvidenceCollector()
    interview_file = FIXTURES_DIR / "interviews" / "interview-001-synthesis.json"

    pp_only = collector.collect_from_interviews([interview_file])
    assert len(pp_only) == 2
    assert all(e.type == EvidenceType.INTERVIEW for e in pp_only)
    assert all(e.quality == EvidenceQuality.PRIMARY for e in pp_only)
    assert pp_only[0].sample_size == 1
    assert "Biometric" in pp_only[0].claim

    with_jtbd = collector.collect_from_interviews([interview_file], include_jtbd=True)
    assert len(with_jtbd) == 3
    assert any("Customer job:" in e.claim for e in with_jtbd)


@pytest.mark.unit
def test_collect_from_in_memory_interview_insights() -> None:
    """collect_from_interview_insights bridges EVO-022 InterviewInsights objects directly."""
    insights = InterviewInsights(
        interview_id="INT-MEM",
        pain_points=[
            PainPoint(
                description="Slow PIX settlement",
                severity=PainPointSeverity.HIGH,
                frequency=2,
                quotes=["Takes forever"],
                confidence=0.9,
                interview_id="INT-MEM",
            )
        ],
        jobs_to_be_done=[
            JobToBeDone(
                job="Pay utility bills instantly",
                situation="Monthly bills",
                motivation="Avoid late fees",
                expected_outcome="Instant confirmation",
                current_solution="Manual banking app",
                quotes=[],
                interview_id="INT-MEM",
            )
        ],
        quotes=[],
        themes=["Speed"],
        summary="Summary",
    )
    collector = EvidenceCollector()
    evidence = collector.collect_from_interview_insights([insights], include_jtbd=True)
    assert len(evidence) == 2
    assert evidence[0].source == "interview:INT-MEM"
    assert evidence[0].confidence == pytest.approx(0.9)


@pytest.mark.unit
def test_collect_from_support_tickets_json() -> None:
    """Support tickets JSON aggregates counts, platforms, examples, and latest timestamp."""
    collector = EvidenceCollector()
    ticket_file = FIXTURES_DIR / "evidence" / "support-tickets.json"

    evidence = collector.collect_from_support_tickets(ticket_file)
    by_id = {e.id: e for e in evidence}
    assert "TICKET-biometric_failures" in by_id
    bio = by_id["TICKET-biometric_failures"]
    assert bio.type == EvidenceType.SUPPORT_TICKET
    assert bio.quality == EvidenceQuality.PRIMARY
    assert bio.sample_size == 3
    assert "Android" in bio.claim
    assert len(bio.supporting_data["examples"]) == 3


@pytest.mark.unit
def test_collect_from_support_tickets_csv(tmp_path: Path) -> None:
    """Support tickets CSV export parses cleanly into grouped ticket evidence."""
    csv_file = tmp_path / "tickets.csv"
    csv_file.write_text(
        "id,issue_type,platform,date,description\n"
        "1,biometric_crash,Android,2026-11-18T10:00:00+00:00,Crash on fingerprint\n"
        "2,biometric_crash,Android,2026-11-19T10:00:00+00:00,Prompt freezes\n",
        encoding="utf-8",
    )
    collector = EvidenceCollector()
    evidence = collector.collect_from_support_tickets(csv_file)
    assert len(evidence) == 1
    assert evidence[0].sample_size == 2
    assert evidence[0].supporting_data["frequency"] == 2


@pytest.mark.unit
def test_collect_from_analytics_dict_and_file() -> None:
    """Analytics collector accepts both inline dicts and JSON file paths."""
    collector = EvidenceCollector()
    from_dict = collector.collect_from_analytics(
        {
            "biometric_adoption_rate": {"value": 0.15, "sample_size": 10000},
            "transfer_completion_rate": {"value": 0.94, "sample_size": 25000},
            "avg_transfer_time": {"value": 11.4, "sample_size": 5000},
            "error_rate": {"value": 0.03, "sample_size": 1000},
        }
    )
    claims = {e.id: e.claim for e in from_dict}
    assert claims["ANALYTICS-biometric_adoption_rate"] == "Biometric adoption rate is 15.0%"
    assert claims["ANALYTICS-transfer_completion_rate"] == "Transfer completion rate is 94.0%"
    assert claims["ANALYTICS-avg_transfer_time"] == "Average transfer time is 11.4 seconds"
    assert claims["ANALYTICS-error_rate"] == "Error rate is 3.0%"

    from_file = collector.collect_from_analytics(
        FIXTURES_DIR / "evidence" / "analytics-report.json"
    )
    assert len(from_file) == 2
    assert from_file[0].type == EvidenceType.ANALYTICS
    assert from_file[0].confidence == pytest.approx(0.95)


@pytest.mark.unit
def test_collect_from_competitor_research_markdown() -> None:
    """Competitor research Markdown headings and bullets become secondary evidence."""
    collector = EvidenceCollector()
    doc = FIXTURES_DIR / "evidence" / "competitor-research.md"
    evidence = collector.collect_from_competitor_research(doc)

    assert len(evidence) >= 2
    assert all(e.type == EvidenceType.COMPETITOR_RESEARCH for e in evidence)
    assert all(e.quality == EvidenceQuality.SECONDARY for e in evidence)
    assert any("Transfer speed is not important" in e.claim for e in evidence)


@pytest.mark.unit
def test_collect_from_urls_with_and_without_fetcher() -> None:
    """URL collector supports injected content fetchers and safe offline URL records."""
    collector = EvidenceCollector()

    offline = collector.collect_from_urls(["https://example.com/payments-report"])
    assert len(offline) == 1
    assert offline[0].type == EvidenceType.URL_ARTICLE
    assert offline[0].quality == EvidenceQuality.SECONDARY

    fetched = collector.collect_from_urls(
        ["https://example.com/study"],
        content_fetcher=lambda url: "## Instant payments drive 22% higher retention\n",
    )
    assert len(fetched) == 1
    assert "22% higher retention" in fetched[0].claim


@pytest.mark.unit
def test_evidence_model_round_trip_and_confidence_clamping() -> None:
    """Evidence serializes cleanly via to_dict/from_dict and clamps confidence to [0, 1]."""
    collector = EvidenceCollector()
    items = collector.collect_from_analytics(
        {"custom_metric": {"value": 42, "unit": "ms", "confidence": 1.5}}
    )
    ev = items[0]
    assert ev.confidence == 1.0
    assert ev.claim == "Custom metric is 42 ms"

    restored = type(ev).from_dict(ev.to_dict())
    assert restored.id == ev.id
    assert restored.claim == ev.claim
    assert restored.type == ev.type
