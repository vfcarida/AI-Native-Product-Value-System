"""
Data models for Architecture Decision Records (ADRs) management (EVO-018).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ADRStatus(str, Enum):
    """Supported ADR lifecycle states."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"


VALID_ADR_STATUSES: frozenset[str] = frozenset(s.value for s in ADRStatus)


@dataclass
class ADRMetadata:
    """Structured ADR metadata parsed from embedded YAML metadata block."""

    id: str
    status: str  # proposed, accepted, deprecated, superseded
    date: str
    authors: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    related_requirements: list[str] = field(default_factory=list)
    related_code: list[str] = field(default_factory=list)
    related_prs: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        normalized = self.status.strip().lower()
        if normalized not in VALID_ADR_STATUSES:
            raise ValueError(
                f"Invalid ADR status {self.status!r}; expected one of {sorted(VALID_ADR_STATUSES)}"
            )
        self.status = normalized

    def to_dict(self) -> dict[str, Any]:
        """Serialize metadata to YAML/JSON dictionary."""
        payload: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "date": self.date,
            "authors": list(self.authors),
            "supersedes": list(self.supersedes),
            "related_requirements": list(self.related_requirements),
            "related_code": list(self.related_code),
            "tags": list(self.tags),
        }
        if self.related_prs:
            payload["related_prs"] = list(self.related_prs)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ADRMetadata:
        """Deserialize metadata from parsed YAML dictionary."""
        return cls(
            id=str(data["id"]),
            status=str(data.get("status", ADRStatus.PROPOSED.value)),
            date=str(data.get("date", "")),
            authors=[str(a) for a in data.get("authors", [])],
            supersedes=[str(s) for s in data.get("supersedes", [])],
            related_requirements=[str(r) for r in data.get("related_requirements", [])],
            related_code=[str(c) for c in data.get("related_code", [])],
            related_prs=[str(p) for p in data.get("related_prs", [])],
            tags=[str(t) for t in data.get("tags", [])],
        )


@dataclass
class ADR:
    """Architecture Decision Record document."""

    metadata: ADRMetadata
    title: str
    context: str
    decision: str
    alternatives: list[dict[str, str]] = field(default_factory=list)
    consequences: dict[str, list[str]] = field(
        default_factory=lambda: {"positive": [], "negative": [], "neutral": []}
    )
    implementation_plan: str = ""
    validation: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    file_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize full ADR to dictionary."""
        return {
            "metadata": self.metadata.to_dict(),
            "title": self.title,
            "context": self.context,
            "decision": self.decision,
            "alternatives": list(self.alternatives),
            "consequences": {k: list(v) for k, v in self.consequences.items()},
            "implementation_plan": self.implementation_plan,
            "validation": list(self.validation),
            "references": list(self.references),
            "file_path": self.file_path,
        }
