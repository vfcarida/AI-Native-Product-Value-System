"""
Unit tests for TranscriptProcessor (VTT, SRT, plain text) (EVO-022).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from product_value_system.discovery.interview_synthesis import (
    TranscriptProcessor,
    format_timestamp,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.mark.unit
def test_parse_webvtt_with_voice_tags() -> None:
    """WebVTT cues with <v Speaker> tags are parsed with timestamps and speaker counts."""
    vtt = """WEBVTT

00:00:01.000 --> 00:00:05.500
<v Interviewer>How do you handle instant transfers?

00:00:06.200 --> 00:00:14.000
<v Participant>It takes forever and I always wait 15 seconds.

00:01:02.000 --> 00:01:10.000
<v Participant>I just want transfers to finish immediately.
"""
    processor = TranscriptProcessor()
    result = processor.process_transcript(vtt, interview_id="INT-VTT-1")

    assert result.interview_id == "INT-VTT-1"
    assert result.source_format == "vtt"
    assert len(result.segments) == 3
    assert result.participant_count == 1
    assert result.duration == timedelta(minutes=1, seconds=2)
    assert result.segments[0].speaker == "Interviewer"
    assert result.segments[1].speaker == "Participant"
    assert "takes forever" in result.segments[1].text
    assert result.segments[2].turn_number == 2


@pytest.mark.unit
def test_parse_webvtt_short_timestamps_and_crlf() -> None:
    """WebVTT supports MM:SS.mmm cues, Speaker: Text syntax, and Windows CRLF line endings."""
    vtt = (
        "WEBVTT\r\n\r\n"
        "00:10.500 --> 00:15.000\r\n"
        "Moderator: Walk me through checkout.\r\n\r\n"
        "01:25.000 --> 01:32.000\r\n"
        "Customer_A: The confirmation screen hangs.\r\n"
    )
    processor = TranscriptProcessor()
    result = processor.process_transcript(vtt, interview_id="INT-VTT-2")

    assert result.source_format == "vtt"
    assert len(result.segments) == 2
    assert result.segments[0].timestamp == timedelta(seconds=10.5)
    assert result.segments[1].speaker == "Customer_A"
    assert result.participant_count == 1


@pytest.mark.unit
def test_parse_srt_subtitle_format() -> None:
    """SubRip (SRT) blocks with comma milliseconds and multiline content parse cleanly."""
    srt = """1
00:00:02,000 --> 00:00:07,500
Interviewer: What frustrates you most
about biometric verification?

2
00:00:08,250 --> 00:00:16,000
Participant: Face recognition fails in low light
and forces me to re-enter my PIN.
"""
    processor = TranscriptProcessor()
    result = processor.process_transcript(srt, interview_id="INT-SRT-1")

    assert result.source_format == "srt"
    assert len(result.segments) == 2
    assert result.segments[0].timestamp == timedelta(seconds=2)
    assert "biometric verification?" in result.segments[0].text
    assert result.segments[1].timestamp == timedelta(seconds=8, milliseconds=250)
    assert result.segments[1].speaker == "Participant"
    assert "re-enter my PIN" in result.segments[1].text


@pytest.mark.unit
def test_parse_plain_text_with_timestamps() -> None:
    """Plain text lines with [HH:MM:SS] and [MM:SS] timestamps parse accurately."""
    plain = """
[00:01:30] Interviewer: What's your biggest frustration with transfers?
[00:01:35] Participant: It takes forever! Sometimes 10-15 seconds just waiting.
[02:10] Interviewer: How often do you make transfers?
[02:15] Participant: Daily, sometimes 5-10 times per day.
"""
    processor = TranscriptProcessor()
    result = processor.process_transcript(plain, interview_id="INT-TXT-1")

    assert result.source_format == "plain"
    assert len(result.segments) == 4
    assert result.segments[0].timestamp == timedelta(minutes=1, seconds=30)
    assert result.segments[2].timestamp == timedelta(minutes=2, seconds=10)
    assert result.segments[3].timestamp == timedelta(minutes=2, seconds=15)
    assert result.duration == timedelta(minutes=2, seconds=15)
    assert result.participant_count == 1


@pytest.mark.unit
def test_parse_plain_text_without_timestamps_or_speakers() -> None:
    """Plain text without timestamps or speaker prefixes falls back safely."""
    plain = "Participant: Error messages are confusing.\nNo speaker prefix on this line."
    processor = TranscriptProcessor()
    result = processor.process_transcript(plain, interview_id="INT-PLAIN-2")

    assert len(result.segments) == 2
    assert result.segments[0].speaker == "Participant"
    assert result.segments[1].speaker == "Unknown"
    assert result.segments[0].line_number == 1
    assert result.segments[1].line_number == 2


@pytest.mark.unit
def test_process_file_loads_fixture() -> None:
    """process_file loads sample-interview.txt and populates source_file metadata."""
    sample = FIXTURES_DIR / "sample-interview.txt"
    processor = TranscriptProcessor()
    result = processor.process_file(sample)

    assert result.interview_id == "SAMPLE-INTERVIEW"
    assert len(result.segments) == 8
    assert result.metadata["source_file"] == str(sample)
    assert result.duration == timedelta(minutes=3, seconds=12)


@pytest.mark.unit
def test_empty_transcript_handled_gracefully() -> None:
    """Empty transcript yields zero segments, zero duration, and non-negative participant count."""
    processor = TranscriptProcessor()
    result = processor.process_transcript("   \n\n  ", interview_id="INT-EMPTY")

    assert result.segments == []
    assert result.duration == timedelta(0)
    assert result.participant_count == 0


@pytest.mark.unit
def test_processed_transcript_serialization_and_timestamp_formatting() -> None:
    """ProcessedTranscript serializes to dict and formats HH:MM:SS timestamps cleanly."""
    assert format_timestamp(timedelta(hours=1, minutes=2, seconds=9)) == "01:02:09"
    processor = TranscriptProcessor()
    result = processor.process_transcript(
        "[00:05:03] Participant: Instant receipts help trust.",
        interview_id="INT-SER",
    )
    payload = result.to_dict()
    assert payload["interview_id"] == "INT-SER"
    assert payload["duration"] == "00:05:03"
    assert payload["segment_count"] == 1
    assert "[00:05:03] Participant:" in result.full_text()
