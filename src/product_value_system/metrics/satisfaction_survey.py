"""
Voluntary, anonymized developer satisfaction pulse surveys (EVO-032).

Enforces privacy invariants:
- Developer identifiers are hashed deterministically (no PII stored)
- Email addresses embedded in free-text feedback are scrubbed automatically
- Opt-out developers are respected without penalty
- ``aggregate()`` exposes cohort statistics only (no individual attribution)
"""

from __future__ import annotations

import hashlib
import re
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

DEFAULT_SURVEY_QUESTIONS: list[dict[str, str]] = [
    {
        "id": "tool_satisfaction",
        "text": "How satisfied are you with AI coding tools? (1-5)",
        "type": "rating",
    },
    {
        "id": "cognitive_load",
        "text": "How often do you feel overwhelmed by complexity? (1-5)",
        "type": "rating",
    },
    {
        "id": "flow_state",
        "text": "How much uninterrupted time do you get daily? (hours)",
        "type": "numeric",
    },
    {
        "id": "feedback",
        "text": "What would improve your development experience?",
        "type": "free_text",
    },
]


def anonymize_respondent(developer_id: str) -> str:
    """Hash developer identifier deterministically so survey records carry zero PII."""
    cleaned = developer_id.strip().lower()
    if not cleaned:
        return "dev_anonymous"
    return f"dev_{hashlib.sha256(cleaned.encode('utf-8')).hexdigest()[:10]}"


def _scrub_pii(text: str) -> str:
    return _EMAIL_RE.sub("[REDACTED_EMAIL]", text).strip()


@dataclass
class SurveyQuestion:
    """Single pulse survey prompt."""

    id: str
    text: str
    type: str = "rating"  # 'rating' (1-5), 'numeric', 'free_text'

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "text": self.text, "type": self.type}


@dataclass
class SurveyResponse:
    """Anonymized response submitted by a voluntary respondent."""

    respondent_hash: str
    answers: dict[str, Any]
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class AggregatedSurveyReport:
    """Aggregate-only cohort summary across survey responses (zero individual PII)."""

    response_count: int
    rating_averages: dict[str, float]
    rating_medians: dict[str, float]
    numeric_averages: dict[str, float]
    feedback_count: int
    anonymized_feedback: list[str]
    aggregate_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_count": self.response_count,
            "rating_averages": {k: round(v, 3) for k, v in self.rating_averages.items()},
            "rating_medians": {k: round(v, 3) for k, v in self.rating_medians.items()},
            "numeric_averages": {k: round(v, 3) for k, v in self.numeric_averages.items()},
            "feedback_count": self.feedback_count,
            "anonymized_feedback": list(self.anonymized_feedback),
            "aggregate_only": self.aggregate_only,
        }


class SatisfactionSurvey:
    """Voluntary, anonymized bi-weekly DevX pulse survey collector & aggregator."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        raw = dict(config or {})
        devx_cfg = raw.get("devx", raw) if isinstance(raw, dict) else {}
        survey_cfg = devx_cfg.get("surveys", devx_cfg) if isinstance(devx_cfg, dict) else {}

        self.enabled: bool = bool(survey_cfg.get("enabled", True))
        self.frequency_days: int = max(1, int(survey_cfg.get("frequency_days", 14)))
        self.anonymize: bool = bool(devx_cfg.get("anonymize", True))
        self.aggregate_only: bool = bool(devx_cfg.get("aggregate_only", True))

        q_raw = survey_cfg.get("questions") or DEFAULT_SURVEY_QUESTIONS
        self.questions: list[SurveyQuestion] = [
            q
            if isinstance(q, SurveyQuestion)
            else SurveyQuestion(
                id=str(q["id"]),
                text=str(q["text"]),
                type=str(q.get("type", "rating")),
            )
            for q in q_raw
        ]
        self._questions_by_id = {q.id: q for q in self.questions}
        self._opted_out: set[str] = set()
        self._responses: list[SurveyResponse] = []

    def opt_out(self, developer_id: str) -> None:
        """Record voluntary opt-out for ``developer_id``."""
        self._opted_out.add(anonymize_respondent(developer_id))

    def opt_in(self, developer_id: str) -> None:
        """Remove opt-out flag for ``developer_id``."""
        self._opted_out.discard(anonymize_respondent(developer_id))

    def is_opted_out(self, developer_id: str) -> bool:
        return anonymize_respondent(developer_id) in self._opted_out

    def render_prompt(self) -> str:
        """Render a concise Markdown survey form (<2 minutes to complete)."""
        lines = [
            "# Developer Experience Pulse Survey (Voluntary & Anonymized)",
            "",
            f"_Cadence: every {self.frequency_days} days. Responses are anonymized "
            "and aggregated strictly for engineering improvement._",
            "",
        ]
        for idx, q in enumerate(self.questions, start=1):
            lines.append(f"{idx}. **{q.id}** ({q.type}): {q.text}")
        return "\n".join(lines) + "\n"

    def submit_response(
        self,
        answers: dict[str, Any],
        *,
        developer_id: str = "anonymous",
        submitted_at: datetime | None = None,
    ) -> SurveyResponse | None:
        """Validate, anonymize, and record a voluntary survey response."""
        if not self.enabled:
            return None
        resp_hash = anonymize_respondent(developer_id)
        if resp_hash in self._opted_out:
            return None

        cleaned: dict[str, Any] = {}
        for qid, val in answers.items():
            q = self._questions_by_id.get(qid)
            if q is None:
                continue
            if q.type == "rating":
                num = float(val)
                if not (1.0 <= num <= 5.0):
                    raise ValueError(f"Rating for '{qid}' must be between 1 and 5, got {val}")
                cleaned[qid] = num
            elif q.type == "numeric":
                num = float(val)
                if num < 0.0:
                    raise ValueError(f"Numeric value for '{qid}' must be non-negative, got {val}")
                cleaned[qid] = num
            else:
                cleaned[qid] = _scrub_pii(str(val))

        record = SurveyResponse(
            respondent_hash=resp_hash,
            answers=cleaned,
            submitted_at=submitted_at or datetime.now(UTC),
        )
        self._responses.append(record)
        return record

    def aggregate(self) -> AggregatedSurveyReport:
        """Return anonymized cohort-level aggregations across all recorded responses."""
        rating_buckets: dict[str, list[float]] = {}
        numeric_buckets: dict[str, list[float]] = {}
        feedback_items: list[str] = []

        for resp in self._responses:
            for qid, val in resp.answers.items():
                q = self._questions_by_id.get(qid)
                if q is None:
                    continue
                if q.type == "rating":
                    rating_buckets.setdefault(qid, []).append(float(val))
                elif q.type == "numeric":
                    numeric_buckets.setdefault(qid, []).append(float(val))
                elif q.type == "free_text" and str(val).strip():
                    feedback_items.append(str(val).strip())

        rating_averages = {
            qid: statistics.mean(vals) for qid, vals in sorted(rating_buckets.items()) if vals
        }
        rating_medians = {
            qid: statistics.median(vals) for qid, vals in sorted(rating_buckets.items()) if vals
        }
        numeric_averages = {
            qid: statistics.mean(vals) for qid, vals in sorted(numeric_buckets.items()) if vals
        }

        return AggregatedSurveyReport(
            response_count=len(self._responses),
            rating_averages=rating_averages,
            rating_medians=rating_medians,
            numeric_averages=numeric_averages,
            feedback_count=len(feedback_items),
            anonymized_feedback=feedback_items,
            aggregate_only=self.aggregate_only,
        )
