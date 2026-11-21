"""
LLM-backed and offline-fallback customer interview insight extraction (EVO-022).

Extracts:
- Pain points with severity (critical, high, medium, low) & confidence (0-1)
- Jobs-to-be-Done (JTBD framework)
- Memorable quotes with speaker, context, timestamp & transcript turn traceability
- Recurring interview themes and executive summary
- Human-in-the-loop review status tracking
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from product_value_system.discovery.pii_redactor import PIIRedactor
from product_value_system.discovery.transcript_processor import (
    ProcessedTranscript,
    format_timestamp,
)

logger = logging.getLogger(__name__)

LLMCallable = Callable[..., Awaitable[str] | str]


class PainPointSeverity(str, Enum):
    """Severity classification for a customer pain point."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SEVERITY_RANK: dict[PainPointSeverity, int] = {
    PainPointSeverity.CRITICAL: 4,
    PainPointSeverity.HIGH: 3,
    PainPointSeverity.MEDIUM: 2,
    PainPointSeverity.LOW: 1,
}


class ReviewStatus(str, Enum):
    """Human-in-the-loop validation workflow state for synthesized insights."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"


@dataclass
class PainPoint:
    """Extracted customer pain point with severity, confidence, and source quotes."""

    description: str
    severity: PainPointSeverity
    frequency: int
    quotes: list[str]
    confidence: float
    interview_id: str
    segment_turns: list[int] = field(default_factory=list)
    review_status: ReviewStatus = ReviewStatus.PENDING_REVIEW

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "severity": self.severity.value,
            "frequency": self.frequency,
            "quotes": list(self.quotes),
            "confidence": round(self.confidence, 4),
            "interview_id": self.interview_id,
            "segment_turns": list(self.segment_turns),
            "review_status": self.review_status.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PainPoint:
        return cls(
            description=str(data["description"]),
            severity=_parse_severity(data.get("severity", "medium")),
            frequency=int(data.get("frequency", max(1, len(data.get("quotes", []))))),
            quotes=[str(q) for q in data.get("quotes", [])],
            confidence=_clamp_confidence(data.get("confidence", 0.75)),
            interview_id=str(data.get("interview_id", "")),
            segment_turns=[int(t) for t in data.get("segment_turns", [])],
            review_status=ReviewStatus(
                data.get("review_status", ReviewStatus.PENDING_REVIEW.value)
            ),
        )


@dataclass
class JobToBeDone:
    """Job-to-be-Done (Kalbach / Christensen framework) extracted from an interview."""

    job: str
    situation: str
    motivation: str
    expected_outcome: str
    current_solution: str
    quotes: list[str]
    interview_id: str
    confidence: float = 0.8

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.job,
            "situation": self.situation,
            "motivation": self.motivation,
            "expected_outcome": self.expected_outcome,
            "current_solution": self.current_solution,
            "quotes": list(self.quotes),
            "interview_id": self.interview_id,
            "confidence": round(self.confidence, 4),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobToBeDone:
        return cls(
            job=str(data["job"]),
            situation=str(data.get("situation", "")),
            motivation=str(data.get("motivation", "")),
            expected_outcome=str(data.get("expected_outcome", "")),
            current_solution=str(data.get("current_solution", "")),
            quotes=[str(q) for q in data.get("quotes", [])],
            interview_id=str(data.get("interview_id", "")),
            confidence=_clamp_confidence(data.get("confidence", 0.8)),
        )


@dataclass
class Quote:
    """Memorable verbatim quote with speaker, timestamp, and transcript location."""

    text: str
    speaker: str
    context: str
    timestamp: str
    interview_id: str
    turn_number: int | None = None
    line_number: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "speaker": self.speaker,
            "context": self.context,
            "timestamp": self.timestamp,
            "interview_id": self.interview_id,
            "turn_number": self.turn_number,
            "line_number": self.line_number,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Quote:
        return cls(
            text=str(data["text"]),
            speaker=str(data.get("speaker", "Participant")),
            context=str(data.get("context", "")),
            timestamp=str(data.get("timestamp", "00:00:00")),
            interview_id=str(data.get("interview_id", "")),
            turn_number=data.get("turn_number"),
            line_number=data.get("line_number"),
        )


@dataclass
class InterviewInsights:
    """Synthesized insights for a single customer interview."""

    interview_id: str
    pain_points: list[PainPoint]
    jobs_to_be_done: list[JobToBeDone]
    quotes: list[Quote]
    themes: list[str]
    summary: str
    redaction_counts: dict[str, int] = field(default_factory=dict)
    review_status: ReviewStatus = ReviewStatus.PENDING_REVIEW
    review_notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def approve(self, notes: str = "") -> None:
        """Approve synthesized insights after human review."""
        self.review_status = ReviewStatus.APPROVED
        self.review_notes = notes
        for pp in self.pain_points:
            pp.review_status = ReviewStatus.APPROVED

    def reject(self, notes: str = "") -> None:
        """Reject synthesized insights during human review."""
        self.review_status = ReviewStatus.REJECTED
        self.review_notes = notes
        for pp in self.pain_points:
            pp.review_status = ReviewStatus.REJECTED

    def request_revision(self, notes: str) -> None:
        """Mark insights as needing revision with reviewer feedback."""
        self.review_status = ReviewStatus.NEEDS_REVISION
        self.review_notes = notes

    def to_dict(self) -> dict[str, Any]:
        return {
            "interview_id": self.interview_id,
            "created_at": self.created_at,
            "review_status": self.review_status.value,
            "review_notes": self.review_notes,
            "redaction_counts": dict(self.redaction_counts),
            "pain_points": [p.to_dict() for p in self.pain_points],
            "jobs_to_be_done": [j.to_dict() for j in self.jobs_to_be_done],
            "quotes": [q.to_dict() for q in self.quotes],
            "themes": list(self.themes),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InterviewInsights:
        return cls(
            interview_id=str(data["interview_id"]),
            pain_points=[PainPoint.from_dict(p) for p in data.get("pain_points", [])],
            jobs_to_be_done=[JobToBeDone.from_dict(j) for j in data.get("jobs_to_be_done", [])],
            quotes=[Quote.from_dict(q) for q in data.get("quotes", [])],
            themes=[str(t) for t in data.get("themes", [])],
            summary=str(data.get("summary", "")),
            redaction_counts={str(k): int(v) for k, v in data.get("redaction_counts", {}).items()},
            review_status=ReviewStatus(
                data.get("review_status", ReviewStatus.PENDING_REVIEW.value)
            ),
            review_notes=str(data.get("review_notes", "")),
            created_at=str(data.get("created_at", datetime.now(UTC).isoformat())),
        )


def _clamp_confidence(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.75
    return max(0.0, min(1.0, numeric))


def _parse_severity(raw: Any) -> PainPointSeverity:
    normalized = str(raw).strip().lower()
    for member in PainPointSeverity:
        if member.value == normalized:
            return member
    return PainPointSeverity.MEDIUM


def _extract_json_payload(raw_response: str) -> Any:
    """Parse JSON from an LLM response, tolerating Markdown code fences."""
    cleaned = raw_response.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    return json.loads(cleaned)


_PAIN_CUES: list[tuple[re.Pattern[str], PainPointSeverity, str, str]] = [
    (
        re.compile(r"\b(crash|outage|lost money|blocked|cannot complete|unusable|broken)\b", re.I),
        PainPointSeverity.CRITICAL,
        "Critical reliability or workflow blocker",
        "Reliability",
    ),
    (
        re.compile(
            r"\b(slow|forever|waiting|wait|takes too long|seconds|latency|delay|timeout)\b", re.I
        ),
        PainPointSeverity.HIGH,
        "Slow performance and excessive waiting time",
        "Speed",
    ),
    (
        re.compile(
            r"\b(error|failed|fail|doesn'?t work|confus|unclear|never know|wrong|bug)\b", re.I
        ),
        PainPointSeverity.MEDIUM,
        "Unclear errors or failure handling friction",
        "Error Handling",
    ),
    (
        re.compile(
            r"\b(too many steps|confirm \d+ times|biometric|fingerprint|face|"
            r"friction|manual|tedious|hard to)\b",
            re.I,
        ),
        PainPointSeverity.MEDIUM,
        "Excessive workflow steps or authentication friction",
        "Simplicity",
    ),
    (
        re.compile(r"\b(trust|security|safe|worried|scared|privacy)\b", re.I),
        PainPointSeverity.MEDIUM,
        "User trust and security anxiety",
        "Trust",
    ),
]

_JTBD_CUES = re.compile(
    r"\b(want to|need to|trying to|just want|send money|split bills|"
    r"transfer|pay|manage|track|daily|every day)\b",
    re.I,
)


class InsightExtractor:
    """Extract structured insights from processed customer interviews."""

    def __init__(
        self,
        llm_callable: LLMCallable | None = None,
        pii_redactor: PIIRedactor | None = None,
    ) -> None:
        self.llm = llm_callable
        self.pii_redactor = pii_redactor or PIIRedactor()

    async def extract_insights(self, transcript: ProcessedTranscript) -> InterviewInsights:
        """Redact PII first, then extract pain points, JTBD, quotes, themes, and summary."""
        redacted_transcript, redaction_counts = self.pii_redactor.redact_transcript(transcript)
        total_redacted = sum(redaction_counts.values())
        if total_redacted > 0:
            logger.info(
                "Redacted %d PII items from interview %s: %s",
                total_redacted,
                transcript.interview_id,
                redaction_counts,
            )

        redacted_text = redacted_transcript.full_text()

        if self.llm is None:
            return self._extract_offline_heuristic(redacted_transcript, redaction_counts)

        pain_points = await self._extract_pain_points(redacted_transcript, redacted_text)
        jtbd = await self._extract_jtbd(redacted_transcript.interview_id, redacted_text)
        quotes = await self._extract_quotes(redacted_transcript, redacted_text)
        themes = await self._extract_themes(redacted_text)
        summary = await self._generate_summary(pain_points, jtbd, themes)

        return InterviewInsights(
            interview_id=transcript.interview_id,
            pain_points=pain_points,
            jobs_to_be_done=jtbd,
            quotes=quotes,
            themes=themes,
            summary=summary,
            redaction_counts=redaction_counts,
        )

    async def _call_llm(self, prompt: str, temperature: float = 0.2) -> str:
        assert self.llm is not None
        result = self.llm(prompt, temperature=temperature)
        if inspect.isawaitable(result):
            return str(await result)
        return str(result)

    def _find_segment_turns(self, transcript: ProcessedTranscript, quotes: list[str]) -> list[int]:
        turns: list[int] = []
        for quote in quotes:
            q_lower = quote.strip().lower()
            if not q_lower:
                continue
            for seg in transcript.segments:
                if q_lower in seg.text.lower():
                    if seg.turn_number not in turns:
                        turns.append(seg.turn_number)
                    break
        return turns

    async def _extract_pain_points(
        self,
        transcript: ProcessedTranscript,
        text: str,
    ) -> list[PainPoint]:
        prompt = f"""Analyze this customer interview transcript and extract pain points.

Transcript:
{text}

For each pain point, identify:
1. Description (what is the problem?)
2. Severity (critical, high, medium, low)
3. Supporting quotes (exact text from transcript)
4. Confidence (0-1, how sure are you this is a real pain point?)

Output JSON array:
[
  {{
    "description": "...",
    "severity": "high",
    "quotes": ["...", "..."],
    "confidence": 0.9
  }}
]
"""
        response = await self._call_llm(prompt, temperature=0.2)
        items = _extract_json_payload(response)
        pain_points: list[PainPoint] = []
        for item in items:
            quotes = [str(q) for q in item.get("quotes", [])]
            pain_points.append(
                PainPoint(
                    description=str(item["description"]),
                    severity=_parse_severity(item.get("severity", "medium")),
                    frequency=max(1, len(quotes)),
                    quotes=quotes,
                    confidence=_clamp_confidence(item.get("confidence", 0.8)),
                    interview_id=transcript.interview_id,
                    segment_turns=self._find_segment_turns(transcript, quotes),
                )
            )
        return pain_points

    async def _extract_jtbd(self, interview_id: str, text: str) -> list[JobToBeDone]:
        prompt = f"""Analyze this interview using the Jobs-to-be-Done framework.

Transcript:
{text}

For each job, identify:
1. Job: What is the customer trying to accomplish?
2. Situation: When does this job arise?
3. Motivation: Why do they want to do this?
4. Expected Outcome: What defines success?
5. Current Solution: How do they do it today?
6. Supporting quotes

Output JSON array:
[
  {{
    "job": "...",
    "situation": "...",
    "motivation": "...",
    "expected_outcome": "...",
    "current_solution": "...",
    "quotes": ["..."]
  }}
]
"""
        response = await self._call_llm(prompt, temperature=0.2)
        items = _extract_json_payload(response)
        return [
            JobToBeDone(
                job=str(item["job"]),
                situation=str(item.get("situation", "")),
                motivation=str(item.get("motivation", "")),
                expected_outcome=str(item.get("expected_outcome", "")),
                current_solution=str(item.get("current_solution", "")),
                quotes=[str(q) for q in item.get("quotes", [])],
                interview_id=interview_id,
                confidence=_clamp_confidence(item.get("confidence", 0.8)),
            )
            for item in items
        ]

    async def _extract_quotes(
        self,
        transcript: ProcessedTranscript,
        redacted_text: str,
    ) -> list[Quote]:
        prompt = f"""Find the 3-5 most memorable, insightful quotes from this interview.

Transcript:
{redacted_text}

Output JSON array:
[
  {{
    "text": "exact quote",
    "context": "what was being discussed"
  }}
]
"""
        response = await self._call_llm(prompt, temperature=0.3)
        items = _extract_json_payload(response)
        quotes: list[Quote] = []
        for item in items:
            quote_text = str(item["text"])
            speaker = "Participant"
            timestamp = "00:00:00"
            turn_number: int | None = None
            line_number: int | None = None

            for seg in transcript.segments:
                if quote_text.lower() in seg.text.lower():
                    speaker = seg.speaker
                    timestamp = format_timestamp(seg.timestamp)
                    turn_number = seg.turn_number
                    line_number = seg.line_number
                    break

            quotes.append(
                Quote(
                    text=quote_text,
                    speaker=speaker,
                    context=str(item.get("context", "")),
                    timestamp=timestamp,
                    interview_id=transcript.interview_id,
                    turn_number=turn_number,
                    line_number=line_number,
                )
            )
        return quotes

    async def _extract_themes(self, text: str) -> list[str]:
        prompt = f"""Identify 3-5 main themes from this interview.

Transcript:
{text}

Output JSON array of theme strings:
["theme1", "theme2", "theme3"]
"""
        response = await self._call_llm(prompt, temperature=0.2)
        items = _extract_json_payload(response)
        return [str(t) for t in items]

    async def _generate_summary(
        self,
        pain_points: list[PainPoint],
        jtbd: list[JobToBeDone],
        themes: list[str],
    ) -> str:
        prompt = (
            "Generate a concise executive summary (2-3 paragraphs) of these interview insights.\n\n"
            f"Pain Points:\n{[p.description for p in pain_points]}\n\n"
            f"Jobs-to-be-Done:\n{[j.job for j in jtbd]}\n\n"
            f"Themes:\n{themes}\n"
        )
        return (await self._call_llm(prompt, temperature=0.3)).strip()

    def _extract_offline_heuristic(
        self,
        transcript: ProcessedTranscript,
        redaction_counts: dict[str, int],
    ) -> InterviewInsights:
        """Deterministic offline insight extractor for CLI/local usage without LLM API keys."""
        participant_segments = [
            seg
            for seg in transcript.segments
            if seg.speaker.strip().lower() not in {"interviewer", "moderator", "researcher", "host"}
        ]
        if not participant_segments:
            participant_segments = list(transcript.segments)

        pain_points: list[PainPoint] = []
        themes: list[str] = []
        seen_descriptions: set[str] = set()

        for seg in participant_segments:
            for pattern, severity, label, theme in _PAIN_CUES:
                if pattern.search(seg.text):
                    desc = f"{label}: {seg.text[:90].rstrip('. ')}"
                    if label not in seen_descriptions:
                        seen_descriptions.add(label)
                        pain_points.append(
                            PainPoint(
                                description=desc,
                                severity=severity,
                                frequency=1,
                                quotes=[seg.text],
                                confidence=0.72,
                                interview_id=transcript.interview_id,
                                segment_turns=[seg.turn_number],
                            )
                        )
                    else:
                        # Increment frequency and attach additional supporting quote
                        for existing in pain_points:
                            if existing.description.startswith(label):
                                existing.frequency += 1
                                if seg.text not in existing.quotes:
                                    existing.quotes.append(seg.text)
                                if seg.turn_number not in existing.segment_turns:
                                    existing.segment_turns.append(seg.turn_number)
                                existing.confidence = min(0.95, existing.confidence + 0.08)
                                break
                    if theme not in themes:
                        themes.append(theme)

        jtbd_list: list[JobToBeDone] = []
        for idx, seg in enumerate(participant_segments):
            if _JTBD_CUES.search(seg.text):
                prev_prompt = (
                    transcript.segments[seg.turn_number - 1].text
                    if seg.turn_number > 0 and seg.turn_number - 1 < len(transcript.segments)
                    else "Customer workflow discussion"
                )
                jtbd_list.append(
                    JobToBeDone(
                        job=seg.text[:120],
                        situation=prev_prompt[:120],
                        motivation="Complete task reliably with minimal friction",
                        expected_outcome="Fast, predictable, and effortless execution",
                        current_solution="Current manual or high-friction workflow",
                        quotes=[seg.text],
                        interview_id=transcript.interview_id,
                        confidence=0.68,
                    )
                )
                if len(jtbd_list) >= 3:
                    break
            elif idx == 0 and not jtbd_list and len(participant_segments) == 1:
                jtbd_list.append(
                    JobToBeDone(
                        job=seg.text[:120],
                        situation="Customer interview response",
                        motivation="Resolve primary workflow need",
                        expected_outcome="Improved customer experience",
                        current_solution="Existing product workflow",
                        quotes=[seg.text],
                        interview_id=transcript.interview_id,
                        confidence=0.6,
                    )
                )

        # Select top participant quotes with timestamp & turn attribution
        ranked_segments = sorted(
            participant_segments,
            key=lambda s: (
                any(pat.search(s.text) for pat, _, _, _ in _PAIN_CUES),
                len(s.text),
            ),
            reverse=True,
        )[:4]
        quotes = [
            Quote(
                text=seg.text,
                speaker=seg.speaker,
                context=(
                    transcript.segments[seg.turn_number - 1].text
                    if seg.turn_number > 0 and seg.turn_number - 1 < len(transcript.segments)
                    else "Interview discussion"
                ),
                timestamp=format_timestamp(seg.timestamp),
                interview_id=transcript.interview_id,
                turn_number=seg.turn_number,
                line_number=seg.line_number,
            )
            for seg in ranked_segments
        ]

        if not themes:
            themes = ["Customer Feedback"]

        top_pains = ", ".join(p.description for p in pain_points[:3]) or "general workflow feedback"
        summary = (
            f"Interview {transcript.interview_id} surfaced {len(pain_points)} pain point(s) "
            f"and {len(jtbd_list)} job(s)-to-be-done across themes: {', '.join(themes)}. "
            f"Key issues highlighted: {top_pains}."
        )

        return InterviewInsights(
            interview_id=transcript.interview_id,
            pain_points=pain_points,
            jobs_to_be_done=jtbd_list,
            quotes=quotes,
            themes=themes,
            summary=summary,
            redaction_counts=redaction_counts,
        )
