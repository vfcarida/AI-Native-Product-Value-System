"""Code ownership and knowledge-silo (bus-factor) analysis (EVO-030)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from product_value_system.brownfield.models import CommitChange


@dataclass
class Ownership:
    """Ownership distribution and bus-factor risk for a single file."""

    file_path: str
    primary_author: str
    primary_ownership_pct: float
    author_count: int
    author_distribution: dict[str, float]  # author_id -> percentage (0..100)
    bus_factor: int
    is_knowledge_silo: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "primary_author": self.primary_author,
            "primary_ownership_pct": round(self.primary_ownership_pct, 2),
            "author_count": self.author_count,
            "author_distribution": {
                k: round(v, 2) for k, v in self.author_distribution.items()
            },
            "bus_factor": self.bus_factor,
            "is_knowledge_silo": self.is_knowledge_silo,
        }


class OwnershipAnalyzer:
    """Identify code ownership distribution, bus factor, and knowledge silos."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.silo_threshold_pct: float = float(cfg.get("silo_threshold_pct", 80.0))

    def analyze(self, changes: list[CommitChange]) -> dict[str, Ownership]:
        """Compute per-file contributor distributions using anonymized author IDs."""
        if not changes:
            return {}

        # Deduplicate per (file_path, commit_hash) so each commit counts once per file
        seen_commits: dict[str, dict[str, str]] = defaultdict(dict)
        for change in changes:
            seen_commits[change.file_path][change.commit_hash] = change.author

        ownership_map: dict[str, Ownership] = {}
        for file_path in sorted(seen_commits):
            counts: Counter[str] = Counter(seen_commits[file_path].values())
            total = sum(counts.values())
            ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

            distribution = {
                author: (cnt / total * 100.0) if total > 0 else 0.0
                for author, cnt in ranked
            }
            primary_author, primary_count = ranked[0]
            primary_pct = (primary_count / total * 100.0) if total > 0 else 0.0

            # Bus factor: fewest authors needed to exceed 50% of commit knowledge
            cumulative = 0.0
            bus_factor = 0
            for _, pct in distribution.items():
                cumulative += pct
                bus_factor += 1
                if cumulative >= 50.0:
                    break

            is_silo = len(ranked) == 1 or primary_pct >= self.silo_threshold_pct
            ownership_map[file_path] = Ownership(
                file_path=file_path,
                primary_author=primary_author,
                primary_ownership_pct=primary_pct,
                author_count=len(ranked),
                author_distribution=distribution,
                bus_factor=bus_factor,
                is_knowledge_silo=is_silo,
            )

        return ownership_map
