"""
End-to-end integration tests for Evidence Synthesis Engine, Agent, Visualization & CLI (EVO-023).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from product_value_system.cli.main import main as cli_main
from product_value_system.evidence import (
    ContradictionDetector,
    Evidence,
    EvidenceCollector,
    EvidenceQuality,
    EvidenceSynthesisAgent,
    EvidenceType,
    QualityScorer,
    SynthesisEngine,
    SynthesisReport,
    render_evidence_network_html,
    render_evidence_network_mermaid,
    render_synthesis_json,
    render_synthesis_markdown,
)
from product_value_system.security.secret_detection import SecretDetector

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_end_to_end_synthesis_with_mock_llm_and_embeddings() -> None:
    """Complete synthesis pipeline with custom LLM & embeddings produces weighted report."""

    async def _mock_embed(texts: list[str]) -> list[list[float]]:
        vecs: list[list[float]] = []
        for t in texts:
            if "biometric" in t.lower():
                vecs.append([1.0, 0.0])
            else:
                vecs.append([0.0, 1.0])
        return vecs

    async def _mock_llm(prompt: str, temperature: float = 0.2) -> str:
        low = prompt.lower()
        if "contradictions" in low:
            if "must-have" in low and "15.0%" in low:
                return json.dumps(
                    {
                        "contradicts": True,
                        "type": "quantitative",
                        "severity": 0.88,
                        "explanation": "Interview calls biometric must-have, analytics shows 15%.",
                        "suggested_research": "Survey 500 active transfer users.",
                    }
                )
            return json.dumps(
                {"contradicts": False, "type": "semantic", "severity": 0.0, "explanation": "ok"}
            )
        if "missing evidence" in low:
            return json.dumps(
                [
                    "No device-level breakdown of biometric failures on Android",
                    "Missing user testing on PIN vs biometric alternatives",
                ]
            )
        return "Biometric adoption remains low (15%) despite qualitative demand in interviews."

    collector = EvidenceCollector()
    evidence: list[Evidence] = []
    evidence.extend(
        collector.collect_from_interviews(
            [FIXTURES_DIR / "interviews" / "interview-001-synthesis.json"]
        )
    )
    evidence.extend(
        collector.collect_from_analytics(
            {"biometric_adoption_rate": {"value": 0.15, "sample_size": 10000}}
        )
    )

    detector = ContradictionDetector(_mock_llm, _mock_embed)
    engine = SynthesisEngine(detector, QualityScorer(), _mock_llm)

    report = await engine.synthesize("Biometric Transfer Feature", evidence)

    assert report.topic == "Biometric Transfer Feature"
    assert len(report.claims) == 2
    assert len(report.contradictions) == 1
    assert report.contradictions[0]["requires_human_review"] is True
    assert len(report.evidence_gaps) == 2
    assert len(report.recommendations) >= 2
    assert len(report.evidence_network) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_higher_quality_evidence_leads_when_contradiction_exists() -> None:
    """High-sample primary analytics + tickets outweigh single interview in conflicting claim."""
    now = datetime.now(UTC)
    ev_analytics = Evidence(
        id="ANA-BIO",
        type=EvidenceType.ANALYTICS,
        quality=EvidenceQuality.PRIMARY,
        source="analytics.json",
        date=now,
        claim="Biometric adoption rate is 2.0%",
        supporting_data={"value": 0.02},
        sample_size=10000,
        confidence=0.95,
    )
    ev_tickets = Evidence(
        id="TKT-BIO",
        type=EvidenceType.SUPPORT_TICKET,
        quality=EvidenceQuality.PRIMARY,
        source="tickets.json",
        date=now,
        claim="Users report 200 support complaints about biometric failures on Android",
        supporting_data={"frequency": 200},
        sample_size=200,
        confidence=0.9,
    )
    ev_interview = Evidence(
        id="INT-BIO",
        type=EvidenceType.INTERVIEW,
        quality=EvidenceQuality.PRIMARY,
        source="interview-001.json",
        date=now,
        claim="Biometric authentication is a must-have feature for transfers",
        supporting_data={},
        sample_size=1,
        confidence=0.8,
    )

    engine = SynthesisEngine()
    report = await engine.synthesize(
        "Biometric Transfer",
        [ev_interview, ev_analytics, ev_tickets],
        similarity_threshold=0.6,
        grouping_threshold=0.6,
    )

    assert len(report.contradictions) == 1
    assert report.contradictions[0]["requires_human_review"] is True

    conflicted_claims = [c for c in report.claims if c.contradicting_evidence]
    assert len(conflicted_claims) == 1
    claim = conflicted_claims[0]
    supporting_ids = {e.id for e in claim.supporting_evidence}
    contradicting_ids = {e.id for e in claim.contradicting_evidence}
    assert "ANA-BIO" in supporting_ids
    assert "INT-BIO" in contradicting_ids
    assert 0.5 < claim.agreement_score < 1.0
    assert "Contradicting:" in claim.provenance
    assert any(
        "TKT-BIO" in {e.id for e in c.supporting_evidence} for c in report.claims
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_synthesis_engine_empty_evidence_returns_graceful_report() -> None:
    """Empty evidence list yields a structured gap & recommendation report without errors."""
    engine = SynthesisEngine()
    report = await engine.synthesize("Empty Topic", [])

    assert report.claims == []
    assert report.contradictions == []
    assert len(report.evidence_gaps) == 1
    assert len(report.recommendations) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_evidence_synthesis_agent_multi_source_and_exports(tmp_path: Path) -> None:
    """EvidenceSynthesisAgent collects all 4 fixture sources and exports MD/JSON/HTML/Mermaid."""
    agent = EvidenceSynthesisAgent()
    report = await agent.synthesize_sources(
        topic="Biometric Transfer Feature",
        interview_files=[FIXTURES_DIR / "interviews" / "interview-001-synthesis.json"],
        ticket_file=FIXTURES_DIR / "evidence" / "support-tickets.json",
        analytics=FIXTURES_DIR / "evidence" / "analytics-report.json",
        research_docs=[FIXTURES_DIR / "evidence" / "competitor-research.md"],
        similarity_threshold=0.6,
    )

    assert len(report.claims) >= 2
    assert len(report.contradictions) >= 1
    assert set(report.metadata["source_types"]) == {
        "analytics",
        "competitor_research",
        "interview",
        "support_ticket",
    }

    md_path = agent.save_report(report, tmp_path / "synthesis.md")
    json_path = agent.save_report(report, tmp_path / "synthesis.json")
    html_path = agent.save_network_html(report, tmp_path / "network.html")

    md_text = md_path.read_text(encoding="utf-8")
    assert "# Evidence Synthesis: Biometric Transfer Feature" in md_text
    assert "## Contradictions" in md_text
    assert "```mermaid" in md_text

    restored = SynthesisReport.from_dict(json.loads(json_path.read_text(encoding="utf-8")))
    assert restored.topic == report.topic
    assert len(restored.claims) == len(report.claims)

    html_text = html_path.read_text(encoding="utf-8")
    assert "<svg id=\"network\"" in html_text
    assert "Biometric Transfer Feature" in html_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reporter_renderers_produce_valid_json_markdown_mermaid_and_html() -> None:
    """Direct reporter helpers produce valid JSON, Markdown, Mermaid, and HTML outputs."""
    agent = EvidenceSynthesisAgent()
    report = await agent.synthesize_sources(
        topic="Transfer Speed",
        analytics={"avg_transfer_time": {"value": 11.4, "sample_size": 5000}},
        urls=["https://example.com/payments"],
    )

    assert json.loads(render_synthesis_json(report))["topic"] == "Transfer Speed"
    assert "## Synthesized Claims" in render_synthesis_markdown(report)
    mermaid = render_evidence_network_mermaid(report)
    assert mermaid.startswith("flowchart LR")
    assert "-->|supports|" in mermaid
    assert "<!DOCTYPE html>" in render_evidence_network_html(report)


@pytest.mark.integration
def test_cli_evidence_synthesize_and_visualize(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI `pvs evidence synthesize` and `pvs evidence visualize` run end-to-end."""
    synth_json = tmp_path / "biometric.json"
    synth_md = tmp_path / "biometric.md"
    net_html = tmp_path / "evidence-network.html"

    rc_json = cli_main(
        [
            "evidence",
            "synthesize",
            "Biometric Transfer",
            "--interviews",
            str(FIXTURES_DIR / "interviews" / "interview-001-synthesis.json"),
            "--tickets",
            str(FIXTURES_DIR / "evidence" / "support-tickets.json"),
            "--analytics",
            str(FIXTURES_DIR / "evidence" / "analytics-report.json"),
            "--research",
            str(FIXTURES_DIR / "evidence" / "competitor-research.md"),
            "--threshold",
            "0.6",
            "--output",
            str(synth_json),
        ]
    )
    assert rc_json == 0
    assert synth_json.exists()
    data = json.loads(synth_json.read_text(encoding="utf-8"))
    assert data["topic"] == "Biometric Transfer"
    assert len(data["claims"]) >= 2

    rc_md = cli_main(
        [
            "evidence",
            "synthesize",
            "Biometric Transfer",
            "--interviews",
            str(FIXTURES_DIR / "interviews"),
            "--output",
            str(synth_md),
            "--network-html",
            str(net_html),
        ]
    )
    assert rc_md == 0
    assert synth_md.exists()
    assert net_html.exists()

    vis_html = tmp_path / "vis.html"
    assert (
        cli_main(
            [
                "evidence",
                "visualize",
                str(synth_json),
                "--format",
                "html",
                "--output",
                str(vis_html),
            ]
        )
        == 0
    )
    assert "<svg id=\"network\"" in vis_html.read_text(encoding="utf-8")

    capsys.readouterr()
    assert cli_main(["evidence", "visualize", str(synth_json), "--format", "mermaid"]) == 0
    assert "flowchart LR" in capsys.readouterr().out


@pytest.mark.unit
def test_cli_evidence_missing_file_returns_nonzero() -> None:
    """CLI returns exit code 1 when passed nonexistent ticket or visualization files."""
    assert (
        cli_main(["evidence", "synthesize", "Topic", "--tickets", "nonexistent_tickets.json"])
        == 1
    )
    assert cli_main(["evidence", "visualize", "nonexistent_synthesis.json"]) == 1


@pytest.mark.unit
def test_evidence_fixtures_and_docs_contain_zero_secrets() -> None:
    """EVO-023 fixtures and documentation contain zero detectable secrets."""
    detector = SecretDetector()
    paths = [
        FIXTURES_DIR / "interviews" / "interview-001-synthesis.json",
        FIXTURES_DIR / "evidence" / "support-tickets.json",
        FIXTURES_DIR / "evidence" / "analytics-report.json",
        FIXTURES_DIR / "evidence" / "competitor-research.md",
        REPO_ROOT / "docs" / "evidence-synthesis.md",
    ]
    for path in paths:
        assert detector.find_secrets(path.read_text(encoding="utf-8")) == [], str(path)
