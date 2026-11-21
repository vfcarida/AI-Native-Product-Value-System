"""
End-to-end integration tests for Customer Interview Synthesis pipeline & CLI (EVO-022).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_value_system.cli.main import main as cli_main
from product_value_system.discovery import (
    InsightExtractor,
    InterviewSynthesizer,
    PIIRedactor,
    TranscriptProcessor,
)
from product_value_system.security.secret_detection import SecretDetector

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.integration
@pytest.mark.asyncio
async def test_end_to_end_synthesis_pipeline() -> None:
    """Complete pipeline: parse -> redact PII -> extract pain points, JTBD, quotes & summary."""
    transcript_text = """
[00:01:30] Interviewer: What's your biggest frustration with transfers?
[00:01:35] Participant: It takes forever! Sometimes 10-15 seconds just waiting.
[00:02:10] Interviewer: How often do you make transfers?
[00:02:15] Participant: Daily, sometimes 5-10 times per day.
"""

    async def _mock_llm(prompt: str, temperature: float = 0.2) -> str:
        if "pain points" in prompt.lower():
            return json.dumps(
                [
                    {
                        "description": "Transfers are too slow with 10-15s waiting time",
                        "severity": "high",
                        "quotes": ["It takes forever! Sometimes 10-15 seconds just waiting."],
                        "confidence": 0.91,
                    }
                ]
            )
        if "jobs-to-be-done" in prompt.lower():
            return json.dumps(
                [
                    {
                        "job": "Send frequent daily transfers quickly",
                        "situation": "5-10 daily payments",
                        "motivation": "Save time during busy workdays",
                        "expected_outcome": "Instant sub-second completion",
                        "current_solution": "Waiting 10-15s per transfer",
                        "quotes": ["Daily, sometimes 5-10 times per day."],
                    }
                ]
            )
        if "memorable, insightful quotes" in prompt.lower():
            return json.dumps(
                [
                    {
                        "text": "It takes forever! Sometimes 10-15 seconds just waiting.",
                        "context": "Transfer latency frustration",
                    }
                ]
            )
        if "main themes" in prompt.lower():
            return json.dumps(["Speed", "Daily Utility"])
        return "High-frequency users experience major frustration due to slow transfer latency."

    processor = TranscriptProcessor()
    transcript = processor.process_transcript(transcript_text, interview_id="INT-001")
    assert len(transcript.segments) == 4

    insights = await InsightExtractor(_mock_llm, PIIRedactor()).extract_insights(transcript)
    assert len(insights.pain_points) > 0
    assert any(
        "slow" in p.description.lower() or "wait" in p.description.lower()
        for p in insights.pain_points
    )
    assert len(insights.jobs_to_be_done) == 1
    assert len(insights.quotes) == 1
    assert insights.quotes[0].timestamp == "00:01:35"
    assert insights.summary != ""


@pytest.mark.integration
@pytest.mark.asyncio
async def test_synthesizer_batch_clustering_and_opportunity_map(tmp_path: Path) -> None:
    """InterviewSynthesizer processes a directory, clusters themes, and builds OpportunityMap."""
    t_dir = tmp_path / "transcripts"
    out_dir = tmp_path / "analysis"
    t_dir.mkdir()

    (t_dir / "int-001.txt").write_text(
        "[00:00:10] Participant: Transfers take forever, waiting 15 seconds is painful.\n"
        "[00:00:30] Participant: The error message is unclear when something fails.\n",
        encoding="utf-8",
    )
    (t_dir / "int-002.vtt").write_text(
        "WEBVTT\n\n"
        "00:00:12.000 --> 00:00:20.000\n"
        "<v Participant>Waiting too long for transfers to finish, need faster speed.\n",
        encoding="utf-8",
    )
    (t_dir / "int-003.srt").write_text(
        "1\n"
        "00:00:05,000 --> 00:00:12,000\n"
        "Participant: Transfer is too slow and sometimes shows a confusing error.\n",
        encoding="utf-8",
    )

    synthesizer = InterviewSynthesizer()
    batch = await synthesizer.synthesize_batch(t_dir, output_dir=out_dir, export_format="json")
    assert len(batch) == 3
    assert len(list(out_dir.glob("*.json"))) == 3

    themes = await synthesizer.cluster_themes(batch, similarity_threshold=0.55)
    assert len(themes) >= 1
    assert any(t.theme_name == "Transfer Speed" and t.frequency == 3 for t in themes)

    omap = await synthesizer.populate_opportunity_map(batch, similarity_threshold=0.55)
    assert len(omap.opportunities) >= 1
    assert omap.prioritized()[0].name == "Transfer Speed"


@pytest.mark.integration
def test_cli_interview_synthesize_single_file(tmp_path: Path) -> None:
    """CLI `pvs interview synthesize` parses sample-interview.txt and saves JSON output."""
    sample = REPO_ROOT / "tests" / "fixtures" / "sample-interview.txt"
    out_file = tmp_path / "int-001.json"

    rc = cli_main(
        [
            "interview",
            "synthesize",
            str(sample),
            "--interview-id",
            "INT-001",
            "--output",
            str(out_file),
            "--format",
            "json",
        ]
    )
    assert rc == 0
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["interview_id"] == "INT-001"
    assert len(data["pain_points"]) >= 2
    assert len(data["quotes"]) >= 1


@pytest.mark.integration
def test_cli_batch_cluster_export_and_dashboard(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI supports synthesize-batch, cluster-themes (+ opportunity-map), export, and dashboard."""
    t_dir = tmp_path / "transcripts"
    analysis_dir = tmp_path / "analysis"
    t_dir.mkdir()

    (t_dir / "a.txt").write_text(
        "[00:00:10] Participant: Transfers take forever, waiting 15 seconds.\n",
        encoding="utf-8",
    )
    (t_dir / "b.txt").write_text(
        "[00:00:20] Participant: Transfers are too slow, need faster processing.\n",
        encoding="utf-8",
    )

    assert (
        cli_main(
            [
                "interview",
                "synthesize-batch",
                str(t_dir),
                "--output",
                str(analysis_dir),
                "--format",
                "json",
            ]
        )
        == 0
    )
    synth_files = sorted(analysis_dir.glob("*.json"))
    assert len(synth_files) == 2

    opp_map_file = tmp_path / "opportunity_map.json"
    cluster_md = tmp_path / "clusters.md"
    assert (
        cli_main(
            [
                "interview",
                "cluster-themes",
                str(analysis_dir),
                "--threshold",
                "0.55",
                "--output",
                str(cluster_md),
                "--opportunity-map",
                str(opp_map_file),
            ]
        )
        == 0
    )
    assert cluster_md.exists()
    assert opp_map_file.exists()
    assert "Transfer Speed" in cluster_md.read_text(encoding="utf-8")

    notion_out = tmp_path / "a-notion.md"
    assert (
        cli_main(
            [
                "interview",
                "export",
                str(synth_files[0]),
                "--format",
                "notion",
                "--output",
                str(notion_out),
            ]
        )
        == 0
    )
    assert "| Property | Value |" in notion_out.read_text(encoding="utf-8")

    capsys.readouterr()
    assert cli_main(["interview", "dashboard", str(analysis_dir), "--format", "json"]) == 0
    dash_data = json.loads(capsys.readouterr().out)
    assert dash_data["total_interviews"] == 2
    assert dash_data["total_pain_points"] >= 2


@pytest.mark.unit
def test_fixture_and_docs_contain_zero_secrets() -> None:
    """EVO-022 fixture and documentation contain zero detectable secrets."""
    detector = SecretDetector()
    fixture_text = (REPO_ROOT / "tests" / "fixtures" / "sample-interview.txt").read_text(
        encoding="utf-8"
    )
    doc_text = (REPO_ROOT / "docs" / "interview-synthesis.md").read_text(encoding="utf-8")

    assert detector.find_secrets(fixture_text) == []
    assert detector.find_secrets(doc_text) == []
