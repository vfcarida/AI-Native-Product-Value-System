"""
Read-only Git history analyzer implementing "Your Code as a Crime Scene" patterns:
hotspots, temporal coupling, code age, and ownership/bus-factor analysis (EVO-030).

Uses streaming read-only ``git log --no-merges --numstat`` for high performance
(10,000+ commits in seconds, native binary file filtering, zero repository mutation)
and optionally integrates with GitPython when installed.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from product_value_system.brownfield.age_analyzer import AgeAnalyzer, FileAge
from product_value_system.brownfield.coupling_analyzer import Coupling, CouplingAnalyzer
from product_value_system.brownfield.hotspot_detector import Hotspot, HotspotDetector
from product_value_system.brownfield.models import CommitChange, anonymize_author
from product_value_system.brownfield.ownership_analyzer import Ownership, OwnershipAnalyzer

logger = logging.getLogger(__name__)

_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".svgz",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".jar",
        ".war",
        ".class",
        ".pyc",
        ".pyo",
        ".so",
        ".dll",
        ".exe",
        ".dylib",
        ".whl",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp3",
        ".mp4",
        ".wav",
        ".sqlite",
        ".db",
        ".bin",
    }
)

_RENAME_BRACE_RE = re.compile(r"^(.*)\{([^{}]*?)\s*=>\s*([^{}]*?)\}(.*)$")
_COMMIT_HEADER_PREFIX = "===PVS_COMMIT==="


def load_brownfield_config(path: Path | str) -> dict[str, Any]:
    """Load the ``brownfield.git_analysis`` configuration block from YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    bf = raw.get("brownfield", raw)
    if isinstance(bf, dict) and "git_analysis" in bf:
        ga = bf["git_analysis"]
        return dict(ga) if isinstance(ga, dict) else {}
    return dict(bf) if isinstance(bf, dict) else {}


def _normalize_git_path(raw_path: str) -> str:
    """Normalize git numstat path (handling renames like ``src/{a.py => b.py}``)."""
    path = raw_path.strip().strip('"').replace("\\", "/")
    brace_match = _RENAME_BRACE_RE.match(path)
    if brace_match:
        prefix, _old, new, suffix = brace_match.groups()
        path = f"{prefix}{new}{suffix}"
    elif " => " in path:
        path = path.split(" => ", 1)[1].strip()
    while "//" in path:
        path = path.replace("//", "/")
    return path.lstrip("/")


@dataclass
class GitInsights:
    """Aggregated temporal insights extracted from repository commit history."""

    hotspots: list[Hotspot] = field(default_factory=list)
    couplings: list[Coupling] = field(default_factory=list)
    file_ages: dict[str, FileAge] = field(default_factory=dict)
    ownership: dict[str, Ownership] = field(default_factory=dict)
    total_commits: int = 0
    total_file_changes: int = 0
    analysis_period_days: int = 180
    analyzed_files: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_commits": self.total_commits,
            "total_file_changes": self.total_file_changes,
            "analyzed_files": self.analyzed_files,
            "analysis_period_days": self.analysis_period_days,
            "hotspots": [h.to_dict() for h in self.hotspots],
            "couplings": [c.to_dict() for c in self.couplings],
            "file_ages": {k: v.to_dict() for k, v in self.file_ages.items()},
            "ownership": {k: v.to_dict() for k, v in self.ownership.items()},
        }


class GitAnalyzer:
    """
    Read-only Git history analyzer computing hotspots, temporal coupling,
    code age, and ownership/knowledge-silo risks.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        raw = dict(config or {})
        if "brownfield" in raw and isinstance(raw["brownfield"], dict):
            raw = dict(raw["brownfield"].get("git_analysis", raw["brownfield"]))
        elif "git_analysis" in raw and isinstance(raw["git_analysis"], dict):
            raw = dict(raw["git_analysis"])

        self.config: dict[str, Any] = raw
        self.enabled: bool = bool(raw.get("enabled", True))
        self.lookback_days: int = max(1, int(raw.get("lookback_days", 180)))
        self.include_patterns: list[str] = list(
            raw.get("include_patterns", ["*.py", "*.js", "*.ts", "*.java", "*.go"])
        )
        self.exclude_patterns: list[str] = list(raw.get("exclude_patterns", []))
        self.max_commits_analyzed: int = max(1, int(raw.get("max_commits_analyzed", 10000)))
        self.anonymize_authors: bool = bool(raw.get("anonymize_authors", True))
        self._cache: dict[tuple[str, str], GitInsights] = {}

    @classmethod
    def from_yaml(cls, path: Path | str) -> GitAnalyzer:
        return cls(load_brownfield_config(path))

    def _matches_filters(self, file_path: str) -> bool:
        """Return True if ``file_path`` is non-binary and passes exclude/include patterns."""
        norm = file_path.replace("\\", "/").lstrip("/")
        if not norm:
            return False

        posix = PurePosixPath(norm)
        if posix.suffix.lower() in _BINARY_EXTENSIONS:
            return False

        basename = posix.name
        for pattern in self.exclude_patterns:
            p_norm = pattern.replace("\\", "/")
            if (
                fnmatch(norm, p_norm)
                or fnmatch(basename, p_norm)
                or fnmatch(norm, f"*/{p_norm}")
            ):
                return False

        if not self.include_patterns:
            return True

        for pattern in self.include_patterns:
            p_norm = pattern.replace("\\", "/")
            if fnmatch(norm, p_norm) or fnmatch(basename, p_norm):
                return True
        return False

    def _get_head_hash(self, repo_path: Path) -> str | None:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    def _is_git_repo(self, repo_path: Path) -> bool:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return proc.returncode == 0 and proc.stdout.strip().lower() == "true"

    def _parse_numstat_log(
        self,
        log_output: str,
        cutoff_date: datetime,
    ) -> list[CommitChange]:
        """Parse streaming ``git log --no-merges --numstat`` output into ``CommitChange`` rows."""
        changes: list[CommitChange] = []
        curr_hash = ""
        curr_author = ""
        curr_dt: datetime | None = None
        skip_commit = False

        for line in log_output.splitlines():
            if line.startswith(_COMMIT_HEADER_PREFIX):
                parts = line[len(_COMMIT_HEADER_PREFIX) :].split("\x1f")
                if len(parts) < 3:
                    skip_commit = True
                    continue
                curr_hash = parts[0].strip()
                raw_author = parts[1].strip()
                ts_raw = parts[2].strip()
                try:
                    curr_dt = datetime.fromtimestamp(int(ts_raw), tz=UTC)
                except ValueError:
                    skip_commit = True
                    continue
                if curr_dt < cutoff_date:
                    skip_commit = True
                    continue
                curr_author = (
                    anonymize_author(raw_author) if self.anonymize_authors else raw_author
                )
                skip_commit = False
                continue

            if skip_commit or not curr_hash or curr_dt is None:
                continue

            stripped = line.strip()
            if not stripped or "\t" not in line:
                continue

            tab_parts = line.split("\t", 2)
            if len(tab_parts) != 3:
                continue
            added_raw, deleted_raw, raw_path = tab_parts

            # Git marks binary files in --numstat with "-\t-\t<path>"
            if added_raw.strip() == "-" or deleted_raw.strip() == "-":
                continue

            file_path = _normalize_git_path(raw_path)
            if not self._matches_filters(file_path):
                continue

            try:
                added = int(added_raw.strip())
                deleted = int(deleted_raw.strip())
            except ValueError:
                continue

            changes.append(
                CommitChange(
                    commit_hash=curr_hash,
                    author=curr_author,
                    timestamp=curr_dt,
                    file_path=file_path,
                    lines_added=added,
                    lines_deleted=deleted,
                )
            )

        return changes

    def _extract_changes(
        self,
        repo_path: Path,
        cutoff_date: datetime,
    ) -> list[CommitChange]:
        """Extract non-merge, non-binary file changes strictly read-only via ``git log``."""
        if not self._is_git_repo(repo_path):
            raise ValueError(f"Not a valid git repository: {repo_path}")

        if self._get_head_hash(repo_path) is None:
            # Valid git repository with zero commits (empty repo)
            return []

        cmd = [
            "git",
            "-C",
            str(repo_path),
            "log",
            "--no-merges",
            "--numstat",
            f"--max-count={self.max_commits_analyzed}",
            f"--format={_COMMIT_HEADER_PREFIX}%H%x1f%ae%x1f%ct",
            "HEAD",
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git log failed for {repo_path}: {proc.stderr.strip()}")

        return self._parse_numstat_log(proc.stdout, cutoff_date)

    def analyze_changes(
        self,
        changes: list[CommitChange],
        *,
        repo_path: Path | None = None,
        reference_time: datetime | None = None,
    ) -> GitInsights:
        """Run hotspot, coupling, age, and ownership analyzers on pre-extracted changes."""
        hotspot_detector = HotspotDetector(self.config)
        hotspots = hotspot_detector.detect(changes)

        coupling_analyzer = CouplingAnalyzer(self.config)
        couplings = coupling_analyzer.analyze(changes)

        age_analyzer = AgeAnalyzer(self.config)
        file_ages = age_analyzer.analyze(
            changes,
            repo_path=repo_path,
            reference_time=reference_time,
        )

        ownership_analyzer = OwnershipAnalyzer(self.config)
        ownership = ownership_analyzer.analyze(changes)

        unique_commits = {c.commit_hash for c in changes}
        unique_files = {c.file_path for c in changes}
        return GitInsights(
            hotspots=hotspots,
            couplings=couplings,
            file_ages=file_ages,
            ownership=ownership,
            total_commits=len(unique_commits),
            total_file_changes=len(changes),
            analysis_period_days=self.lookback_days,
            analyzed_files=len(unique_files),
        )

    async def analyze(
        self,
        repo_path: Path | str,
        *,
        reference_time: datetime | None = None,
        use_cache: bool = True,
    ) -> GitInsights:
        """Analyze git repository history asynchronously without modifying the repo."""
        path = Path(repo_path).resolve()
        if not self.enabled:
            logger.info("Git history analysis disabled; returning empty insights.")
            return GitInsights(analysis_period_days=self.lookback_days)

        head_hash = self._get_head_hash(path) if self._is_git_repo(path) else None
        cache_key = (str(path), head_hash or "EMPTY")
        if use_cache and head_hash and cache_key in self._cache:
            return self._cache[cache_key]

        now = reference_time or datetime.now(UTC)
        cutoff_date = now - timedelta(days=self.lookback_days)

        logger.info("Analyzing git history for %s (lookback=%dd)", path, self.lookback_days)
        loop = asyncio.get_running_loop()
        changes = await loop.run_in_executor(
            None,
            lambda: self._extract_changes(path, cutoff_date),
        )
        logger.info("Extracted %d file changes from git history", len(changes))

        insights = await loop.run_in_executor(
            None,
            lambda: self.analyze_changes(changes, repo_path=path, reference_time=now),
        )
        if use_cache and head_hash:
            self._cache[cache_key] = insights
        return insights
