"""
Interview transcript parser supporting WebVTT, SubRip (SRT), and plain text formats (EVO-022).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_INTERVIEWER_LABELS = {"interviewer", "moderator", "researcher", "host", "pm", "facilitator"}


def format_timestamp(value: timedelta) -> str:
    """Format a timedelta as ``HH:MM:SS``."""
    total_seconds = max(0, int(value.total_seconds()))
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


@dataclass
class TranscriptSegment:
    """Single spoken turn in an interview transcript."""

    speaker: str
    text: str
    timestamp: timedelta
    turn_number: int
    line_number: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "text": self.text,
            "timestamp": format_timestamp(self.timestamp),
            "timestamp_seconds": self.timestamp.total_seconds(),
            "turn_number": self.turn_number,
            "line_number": self.line_number,
        }


@dataclass
class ProcessedTranscript:
    """Structured representation of a parsed customer interview transcript."""

    interview_id: str
    date: datetime
    duration: timedelta
    segments: list[TranscriptSegment]
    participant_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
    source_format: str = "plain"

    def full_text(self) -> str:
        """Render transcript turns with timestamps and speaker labels."""
        return "\n".join(
            f"[{format_timestamp(seg.timestamp)}] {seg.speaker}: {seg.text}"
            for seg in self.segments
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "interview_id": self.interview_id,
            "date": self.date.isoformat(),
            "duration": format_timestamp(self.duration),
            "duration_seconds": self.duration.total_seconds(),
            "participant_count": self.participant_count,
            "source_format": self.source_format,
            "segment_count": len(self.segments),
            "segments": [seg.to_dict() for seg in self.segments],
            "metadata": dict(self.metadata),
        }


class TranscriptProcessor:
    """Parse raw interview transcripts (VTT, SRT, plain text) into structured turns."""

    _VTT_TIME_RE = re.compile(
        r"^(?:(?P<h>\d{1,2}):)?(?P<m>\d{2}):(?P<s>\d{2}\.\d{1,3})\s*-->\s*"
        r"(?:(?P<eh>\d{1,2}):)?(?P<em>\d{2}):(?P<es>\d{2}\.\d{1,3})"
    )
    _SRT_TIME_RE = re.compile(
        r"^(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2}),(?P<ms>\d{1,3})\s*-->\s*"
        r"(?P<eh>\d{1,2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{1,3})"
    )
    _PLAIN_TS_RE = re.compile(r"^\[(?:(?P<h>\d{1,2}):)?(?P<m>\d{2}):(?P<s>\d{2})\]\s*")
    _VOICE_TAG_RE = re.compile(r"^<v\s+([^>]+)>(.*)$", re.DOTALL)

    def process_file(
        self,
        file_path: Path | str,
        interview_id: str | None = None,
        interview_date: datetime | None = None,
    ) -> ProcessedTranscript:
        """Load and parse a transcript file from disk."""
        path = Path(file_path)
        text = path.read_text(encoding="utf-8")
        resolved_id = interview_id or path.stem.upper()
        transcript = self.process_transcript(
            transcript_text=text,
            interview_id=resolved_id,
            interview_date=interview_date,
        )
        transcript.metadata["source_file"] = str(path)
        return transcript

    def process_transcript(
        self,
        transcript_text: str,
        interview_id: str,
        interview_date: datetime | None = None,
    ) -> ProcessedTranscript:
        """Detect format (VTT, SRT, or plain text) and parse into segments."""
        normalized = transcript_text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")

        if normalized.lstrip().startswith("WEBVTT"):
            source_format = "vtt"
            segments = self._parse_vtt(normalized)
        elif "-->" in normalized and re.search(
            r"\d+\s*\n\d{1,2}:\d{2}:\d{2},\d{1,3}\s*-->", normalized
        ):
            source_format = "srt"
            segments = self._parse_srt(normalized)
        else:
            source_format = "plain"
            segments = self._parse_plain_text(normalized)

        duration = max((seg.timestamp for seg in segments), default=timedelta(0))
        speakers = {seg.speaker.strip() for seg in segments if seg.speaker.strip()}
        non_interviewers = {
            s for s in speakers if s.lower() not in _INTERVIEWER_LABELS and s.lower() != "unknown"
        }
        if non_interviewers:
            participant_count = len(non_interviewers)
        else:
            participant_count = max(0, len(speakers) - 1) if len(speakers) > 1 else len(speakers)

        return ProcessedTranscript(
            interview_id=interview_id,
            date=interview_date or datetime.now(UTC),
            duration=duration,
            segments=segments,
            participant_count=participant_count,
            metadata={"speakers": sorted(speakers)},
            source_format=source_format,
        )

    def _parse_vtt(self, text: str) -> list[TranscriptSegment]:
        """Parse WebVTT cues supporting ``<v Speaker>text`` and ``Speaker: text``."""
        segments: list[TranscriptSegment] = []
        lines = text.split("\n")
        idx = 0
        total = len(lines)

        while idx < total:
            line = lines[idx].strip()
            match = self._VTT_TIME_RE.match(line)
            if not match:
                idx += 1
                continue

            cue_line_number = idx + 1
            hours = int(match.group("h") or 0)
            minutes = int(match.group("m"))
            seconds = float(match.group("s"))
            timestamp = timedelta(hours=hours, minutes=minutes, seconds=seconds)

            idx += 1
            payload_lines: list[str] = []
            while idx < total and lines[idx].strip() != "":
                payload_lines.append(lines[idx].strip())
                idx += 1

            raw_content = " ".join(payload_lines).strip()
            if not raw_content:
                continue

            speaker, content = self._extract_speaker_and_text(raw_content)
            segments.append(
                TranscriptSegment(
                    speaker=speaker,
                    text=content,
                    timestamp=timestamp,
                    turn_number=len(segments),
                    line_number=cue_line_number,
                )
            )

        return segments

    def _parse_srt(self, text: str) -> list[TranscriptSegment]:
        """Parse SubRip (SRT) subtitle blocks."""
        segments: list[TranscriptSegment] = []
        lines = text.split("\n")
        idx = 0
        total = len(lines)

        while idx < total:
            line = lines[idx].strip()
            match = self._SRT_TIME_RE.match(line)
            if not match:
                idx += 1
                continue

            cue_line_number = idx + 1
            timestamp = timedelta(
                hours=int(match.group("h")),
                minutes=int(match.group("m")),
                seconds=int(match.group("s")),
                milliseconds=int(match.group("ms")),
            )

            idx += 1
            payload_lines: list[str] = []
            while idx < total and lines[idx].strip() != "":
                payload_lines.append(lines[idx].strip())
                idx += 1

            raw_content = " ".join(payload_lines).strip()
            if not raw_content:
                continue

            speaker, content = self._extract_speaker_and_text(raw_content)
            segments.append(
                TranscriptSegment(
                    speaker=speaker,
                    text=content,
                    timestamp=timestamp,
                    turn_number=len(segments),
                    line_number=cue_line_number,
                )
            )

        return segments

    def _parse_plain_text(self, text: str) -> list[TranscriptSegment]:
        """Parse plain text lines with optional ``[HH:MM:SS]`` or ``[MM:SS]`` timestamps."""
        segments: list[TranscriptSegment] = []
        for line_idx, raw_line in enumerate(text.split("\n"), start=1):
            line = raw_line.strip()
            if not line:
                continue

            timestamp = timedelta(0)
            ts_match = self._PLAIN_TS_RE.match(line)
            if ts_match:
                timestamp = timedelta(
                    hours=int(ts_match.group("h") or 0),
                    minutes=int(ts_match.group("m")),
                    seconds=int(ts_match.group("s")),
                )
                line = line[ts_match.end() :].strip()

            speaker, content = self._extract_speaker_and_text(line)
            if not content:
                continue
            segments.append(
                TranscriptSegment(
                    speaker=speaker,
                    text=content,
                    timestamp=timestamp,
                    turn_number=len(segments),
                    line_number=line_idx,
                )
            )

        return segments

    def _extract_speaker_and_text(self, raw_content: str) -> tuple[str, str]:
        """Extract speaker label from ``<v Name>text`` or ``Name: text``."""
        voice_match = self._VOICE_TAG_RE.match(raw_content)
        if voice_match:
            speaker = voice_match.group(1).strip()
            content = re.sub(r"</v>\s*$", "", voice_match.group(2).strip()).strip()
            return speaker or "Unknown", content

        if ":" in raw_content:
            candidate_speaker, rest = raw_content.split(":", 1)
            candidate = candidate_speaker.strip()
            # Guard against timestamps or URLs masquerading as speakers
            if candidate and len(candidate) <= 40 and not candidate.startswith(("http", "https")):
                return candidate, rest.strip()

        return "Unknown", raw_content.strip()
