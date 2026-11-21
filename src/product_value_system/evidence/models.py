"""
Data models for Multi-Source Evidence Synthesis (EVO-023).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class EvidenceType(str, Enum):
    """Origin category of an evidence item."""

    INTERVIEW = "interview"
    SUPPORT_TICKET = "support_ticket"
    ANALYTICS = "analytics"
    COMPETITOR_RESEARCH = "competitor_research"
    USER_STUDY = "user_study"
    SURVEY = "survey"
    URL_ARTICLE = "url_article"


class EvidenceQuality(str, Enum):
    """Epistemic hierarchy rating for evidence sources."""

    PRIMARY = "primary"  # First-hand observation (interviews, analytics, tickets)
    SECONDARY = "secondary"  # Reported by others (articles, competitor research)
    TERTIARY = "tertiary"  # Compilations (reviews, summaries)


@dataclass
class Evidence:
    """Single traceable unit of product evidence."""

    id: str
    type: EvidenceType
    quality: EvidenceQuality
    source: str
    date: datetime
    claim: str
    supporting_data: dict[str, Any] = field(default_factory=dict)
    sample_size: int | None = None
    methodology: str | None = None
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "quality": self.quality.value,
            "source": self.source,
            "date": self.date.isoformat(),
            "claim": self.claim,
            "supporting_data": dict(self.supporting_data),
            "sample_size": self.sample_size,
            "methodology": self.methodology,
            "confidence": round(self.confidence, 4),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Evidence:
        raw_date = data.get("date")
        parsed_date = (
            datetime.fromisoformat(str(raw_date))
            if raw_date
            else datetime.now(UTC)
        )
        return cls(
            id=str(data["id"]),
            type=EvidenceType(data["type"]),
            quality=EvidenceQuality(data["quality"]),
            source=str(data["source"]),
            date=parsed_date,
            claim=str(data["claim"]),
            supporting_data=dict(data.get("supporting_data", {})),
            sample_size=data.get("sample_size"),
            methodology=data.get("methodology"),
            confidence=float(data.get("confidence", 1.0)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class Contradiction:
    """Detected conflict between two pieces of evidence on the same topic."""

    evidence1: Evidence
    evidence2: Evidence
    contradiction_type: str  # semantic, quantitative, temporal
    severity: float  # 0.0 - 1.0
    explanation: str
    requires_human_review: bool = False
    suggested_research: str = ""

    def __post_init__(self) -> None:
        self.severity = max(0.0, min(1.0, float(self.severity)))
        if self.severity >= 0.7:
            self.requires_human_review = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence1_id": self.evidence1.id,
            "evidence2_id": self.evidence2.id,
            "evidence1": self.evidence1.claim,
            "evidence2": self.evidence2.claim,
            "evidence1_source": self.evidence1.source,
            "evidence2_source": self.evidence2.source,
            "type": self.contradiction_type,
            "severity": round(self.severity, 4),
            "explanation": self.explanation,
            "requires_human_review": self.requires_human_review,
            "suggested_research": self.suggested_research,
        }


@dataclass
class Claim:
    """Synthesized product claim backed by weighted multi-source evidence."""

    statement: str
    supporting_evidence: list[Evidence]
    contradicting_evidence: list[Evidence]
    agreement_score: float  # 0.0 - 1.0 (1.0 = full agreement)
    overall_confidence: float  # 0.0 - 1.0
    provenance: str
    quality_scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "supporting_evidence": [e.to_dict() for e in self.supporting_evidence],
            "contradicting_evidence": [e.to_dict() for e in self.contradicting_evidence],
            "agreement_score": round(self.agreement_score, 4),
            "overall_confidence": round(self.overall_confidence, 4),
            "provenance": self.provenance,
            "quality_scores": {k: round(v, 4) for k, v in self.quality_scores.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Claim:
        return cls(
            statement=str(data["statement"]),
            supporting_evidence=[
                Evidence.from_dict(e) for e in data.get("supporting_evidence", [])
            ],
            contradicting_evidence=[
                Evidence.from_dict(e) for e in data.get("contradicting_evidence", [])
            ],
            agreement_score=float(data.get("agreement_score", 1.0)),
            overall_confidence=float(data.get("overall_confidence", 0.5)),
            provenance=str(data.get("provenance", "")),
            quality_scores={
                str(k): float(v) for k, v in data.get("quality_scores", {}).items()
            },
        )


@dataclass
class SynthesisReport:
    """Complete multi-source evidence synthesis report with provenance & network graph."""

    topic: str
    date: datetime
    claims: list[Claim]
    contradictions: list[dict[str, Any]]
    evidence_gaps: list[str]
    recommendations: list[str]
    evidence_network: dict[str, list[str]]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "date": self.date.isoformat(),
            "claims": [c.to_dict() for c in self.claims],
            "contradictions": list(self.contradictions),
            "evidence_gaps": list(self.evidence_gaps),
            "recommendations": list(self.recommendations),
            "evidence_network": {k: list(v) for k, v in self.evidence_network.items()},
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SynthesisReport:
        raw_date = data.get("date")
        parsed_date = (
            datetime.fromisoformat(str(raw_date))
            if raw_date
            else datetime.now(UTC)
        )
        return cls(
            topic=str(data["topic"]),
            date=parsed_date,
            claims=[Claim.from_dict(c) for c in data.get("claims", [])],
            contradictions=list(data.get("contradictions", [])),
            evidence_gaps=[str(g) for g in data.get("evidence_gaps", [])],
            recommendations=[str(r) for r in data.get("recommendations", [])],
            evidence_network={
                str(k): [str(src) for src in v]
                for k, v in data.get("evidence_network", {}).items()
            },
            metadata=dict(data.get("metadata", {})),
        )
