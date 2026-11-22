"""Shared data models for brownfield git history analysis (EVO-030)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any


def anonymize_author(author_identifier: str) -> str:
    """Deterministically anonymize an author email/name so no PII leaks into reports."""
    cleaned = author_identifier.strip().lower()
    if not cleaned:
        return "author_unknown"
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:10]
    return f"author_{digest}"


@dataclass
class CommitChange:
    """Single file modification extracted from a non-merge git commit."""

    commit_hash: str
    author: str
    timestamp: datetime
    file_path: str
    lines_added: int = 0
    lines_deleted: int = 0

    @property
    def lines_changed(self) -> int:
        return self.lines_added + self.lines_deleted

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_hash": self.commit_hash,
            "author": self.author,
            "timestamp": self.timestamp.isoformat(),
            "file_path": self.file_path,
            "lines_added": self.lines_added,
            "lines_deleted": self.lines_deleted,
        }
