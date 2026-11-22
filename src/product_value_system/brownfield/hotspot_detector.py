"""Change hotspot detection for brownfield git history analysis (EVO-030)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from product_value_system.brownfield.models import CommitChange


@dataclass
class Hotspot:
    """High-churn file identified as a refactoring hotspot."""

    file_path: str
    change_count: int
    total_lines_changed: int
    change_frequency: float  # Commits per 30-day month
    rank: int
    is_top_decile: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "change_count": self.change_count,
            "total_lines_changed": self.total_lines_changed,
            "change_frequency": round(self.change_frequency, 3),
            "rank": self.rank,
            "is_top_decile": self.is_top_decile,
        }


class HotspotDetector:
    """Detect high-churn files from commit change history."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.min_commits: int = max(1, int(cfg.get("min_commits_for_hotspot", 10)))
        self.lookback_days: float = max(1.0, float(cfg.get("lookback_days", 180)))

    def detect(self, changes: list[CommitChange]) -> list[Hotspot]:
        """Rank files meeting ``min_commits_for_hotspot`` by descending commit frequency."""
        if not changes:
            return []

        commits_per_file: dict[str, set[str]] = defaultdict(set)
        lines_changed: dict[str, int] = defaultdict(int)

        for change in changes:
            commits_per_file[change.file_path].add(change.commit_hash)
            lines_changed[change.file_path] += change.lines_added + change.lines_deleted

        months = self.lookback_days / 30.0
        hotspots: list[Hotspot] = []
        for file_path, commit_set in commits_per_file.items():
            count = len(commit_set)
            if count >= self.min_commits:
                hotspots.append(
                    Hotspot(
                        file_path=file_path,
                        change_count=count,
                        total_lines_changed=lines_changed[file_path],
                        change_frequency=count / months,
                        rank=0,
                    )
                )

        hotspots.sort(key=lambda h: (-h.change_count, -h.total_lines_changed, h.file_path))

        top_decile_cutoff = max(1, (len(hotspots) + 9) // 10) if hotspots else 0
        for idx, hotspot in enumerate(hotspots):
            hotspot.rank = idx + 1
            hotspot.is_top_decile = idx < top_decile_cutoff

        return hotspots
