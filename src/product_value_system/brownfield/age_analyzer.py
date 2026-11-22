"""Code age and stability analysis for brownfield repositories (EVO-030)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_value_system.brownfield.models import CommitChange


@dataclass
class FileAge:
    """Temporal age, recency, and stability classification for a single file."""

    file_path: str
    first_commit_date: datetime
    last_commit_date: datetime
    age_days: float
    days_since_last_change: float
    commit_count: int
    stability: str  # 'volatile', 'active', 'aging', 'stable_legacy'

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "first_commit_date": self.first_commit_date.isoformat(),
            "last_commit_date": self.last_commit_date.isoformat(),
            "age_days": round(self.age_days, 2),
            "days_since_last_change": round(self.days_since_last_change, 2),
            "commit_count": self.commit_count,
            "stability": self.stability,
        }


class AgeAnalyzer:
    """Compute file age, staleness, and stability classification from git history."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.volatile_commits_threshold: int = int(cfg.get("volatile_commits_threshold", 10))
        self.stable_untouched_days: float = float(cfg.get("stable_untouched_days", 365.0))

    def _classify_stability(
        self,
        age_days: float,
        days_since_last_change: float,
        commit_count: int,
    ) -> str:
        if commit_count >= self.volatile_commits_threshold and days_since_last_change <= 30.0:
            return "volatile"
        if (
            days_since_last_change >= self.stable_untouched_days
            and age_days >= self.stable_untouched_days
        ):
            return "stable_legacy"
        if days_since_last_change <= 90.0:
            return "active"
        return "aging"

    @staticmethod
    def _lookup_repo_birth_dates(
        repo_path: Path,
        target_files: set[str],
    ) -> dict[str, datetime]:
        """Find earliest commit timestamp per file in a single read-only git pass."""
        if not target_files:
            return {}
        try:
            proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_path),
                    "log",
                    "--no-merges",
                    "--format=COMMIT:%ct",
                    "--name-only",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if proc.returncode != 0:
            return {}

        earliest: dict[str, datetime] = {}
        current_dt: datetime | None = None
        for line in proc.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("COMMIT:"):
                ts_str = stripped[7:]
                if ts_str.isdigit():
                    current_dt = datetime.fromtimestamp(int(ts_str), tz=UTC)
            elif current_dt is not None:
                norm = stripped.replace("\\", "/")
                if norm in target_files:
                    prev = earliest.get(norm)
                    if prev is None or current_dt < prev:
                        earliest[norm] = current_dt
        return earliest

    def analyze(
        self,
        changes: list[CommitChange],
        *,
        repo_path: Path | None = None,
        reference_time: datetime | None = None,
    ) -> dict[str, FileAge]:
        """Compute ``FileAge`` for every file present in ``changes``."""
        if not changes:
            return {}

        now = reference_time or datetime.now(UTC)
        per_file_dates: dict[str, list[datetime]] = {}
        per_file_commits: dict[str, set[str]] = {}

        for change in changes:
            per_file_dates.setdefault(change.file_path, []).append(change.timestamp)
            per_file_commits.setdefault(change.file_path, set()).add(change.commit_hash)

        birth_dates: dict[str, datetime] = (
            self._lookup_repo_birth_dates(repo_path, set(per_file_dates.keys()))
            if repo_path is not None
            else {}
        )

        result: dict[str, FileAge] = {}
        for file_path in sorted(per_file_dates):
            dates = per_file_dates[file_path]
            first_dt = min(dates)
            if file_path in birth_dates and birth_dates[file_path] < first_dt:
                first_dt = birth_dates[file_path]
            last_dt = max(dates)
            age_days = max(0.0, (now - first_dt).total_seconds() / 86400.0)
            since_last = max(0.0, (now - last_dt).total_seconds() / 86400.0)
            count = len(per_file_commits[file_path])
            stability = self._classify_stability(age_days, since_last, count)
            result[file_path] = FileAge(
                file_path=file_path,
                first_commit_date=first_dt,
                last_commit_date=last_dt,
                age_days=age_days,
                days_since_last_change=since_last,
                commit_count=count,
                stability=stability,
            )

        return result
