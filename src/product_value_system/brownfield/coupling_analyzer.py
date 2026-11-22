"""Temporal coupling analysis (files frequently modified together) (EVO-030)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from product_value_system.brownfield.models import CommitChange


@dataclass
class Coupling:
    """Measured temporal coupling between two source files."""

    file1: str
    file2: str
    coupling_coefficient: float  # 0.0-1.0
    co_change_count: int
    file1_total_changes: int
    file2_total_changes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "file1": self.file1,
            "file2": self.file2,
            "coupling_coefficient": round(self.coupling_coefficient, 4),
            "co_change_count": self.co_change_count,
            "file1_total_changes": self.file1_total_changes,
            "file2_total_changes": self.file2_total_changes,
        }


class CouplingAnalyzer:
    """Detect hidden architectural dependencies via file co-change matrices."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.threshold: float = float(cfg.get("coupling_threshold", 0.5))
        self.min_co_changes: int = max(1, int(cfg.get("min_co_changes", 2)))
        self.max_files_per_commit: int = max(2, int(cfg.get("max_files_per_commit", 50)))

    def analyze(self, changes: list[CommitChange]) -> list[Coupling]:
        """Compute temporal coupling coefficients for co-changed file pairs."""
        if not changes:
            return []

        commits: dict[str, set[str]] = defaultdict(set)
        file_commits: dict[str, set[str]] = defaultdict(set)

        for change in changes:
            commits[change.commit_hash].add(change.file_path)
            file_commits[change.file_path].add(change.commit_hash)

        co_changes: dict[tuple[str, str], int] = defaultdict(int)
        for files in commits.values():
            if len(files) < 2 or len(files) > self.max_files_per_commit:
                continue
            files_list = sorted(files)
            for idx, file1 in enumerate(files_list):
                for file2 in files_list[idx + 1 :]:
                    co_changes[(file1, file2)] += 1

        couplings: list[Coupling] = []
        for (file1, file2), co_count in co_changes.items():
            if co_count < self.min_co_changes:
                continue
            f1_total = len(file_commits[file1])
            f2_total = len(file_commits[file2])
            min_total = min(f1_total, f2_total)
            coeff = (co_count / min_total) if min_total > 0 else 0.0
            if coeff >= self.threshold:
                couplings.append(
                    Coupling(
                        file1=file1,
                        file2=file2,
                        coupling_coefficient=min(1.0, coeff),
                        co_change_count=co_count,
                        file1_total_changes=f1_total,
                        file2_total_changes=f2_total,
                    )
                )

        couplings.sort(
            key=lambda c: (-c.coupling_coefficient, -c.co_change_count, c.file1, c.file2)
        )
        return couplings
