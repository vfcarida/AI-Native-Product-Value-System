"""Unit tests for EVO-030 GitAnalyzer, HotspotDetector, Coupling, Age & Ownership analyzers."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from product_value_system.brownfield import (
    AgeAnalyzer,
    CommitChange,
    CouplingAnalyzer,
    GitAnalyzer,
    HotspotDetector,
    OwnershipAnalyzer,
    anonymize_author,
    load_brownfield_config,
)
from product_value_system.security.hybrid_detector import HybridSecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str, env_extra: dict[str, str] | None = None) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test Dev",
        "GIT_AUTHOR_EMAIL": "dev@example.org",
        "GIT_COMMITTER_NAME": "Test Dev",
        "GIT_COMMITTER_EMAIL": "dev@example.org",
        **(env_extra or {}),
    }
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Test Dev")
    _git(path, "config", "user.email", "dev@example.org")
    return path


def _commit_file(
    repo: Path,
    rel_path: str,
    content: str | bytes,
    message: str,
    *,
    author_email: str = "dev@example.org",
    commit_dt: datetime | None = None,
) -> str:
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        full.write_bytes(content)
    else:
        full.write_text(content, encoding="utf-8")
    _git(repo, "add", rel_path)
    env_extra: dict[str, str] = {
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_EMAIL": author_email,
    }
    if commit_dt is not None:
        iso = commit_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        env_extra["GIT_AUTHOR_DATE"] = iso
        env_extra["GIT_COMMITTER_DATE"] = iso
    _git(repo, "commit", "-m", message, env_extra=env_extra)
    return _git(repo, "rev-parse", "HEAD")


async def test_extract_changes_from_real_temp_git_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo_basic")
    _commit_file(repo, "src/app.py", "def run():\n    return 1\n", "c1")
    _commit_file(repo, "src/app.py", "def run():\n    return 2\n", "c2")
    _commit_file(repo, "src/util.py", "X = 10\n", "c3")
    head_before = _git(repo, "rev-parse", "HEAD")

    analyzer = GitAnalyzer({"lookback_days": 30, "min_commits_for_hotspot": 2})
    insights = await analyzer.analyze(repo)

    assert insights.total_commits == 3
    assert insights.total_file_changes == 3
    assert len(insights.hotspots) == 1
    assert insights.hotspots[0].file_path == "src/app.py"
    assert insights.hotspots[0].change_count == 2
    # Read-only invariant: HEAD unchanged
    assert _git(repo, "rev-parse", "HEAD") == head_before


def test_file_filter_matching_include_and_exclude() -> None:
    analyzer = GitAnalyzer(
        {
            "include_patterns": ["*.py", "*.js", "*.java", "*.go"],
            "exclude_patterns": ["node_modules/**", "vendor/**", "*.test.py", "*.spec.js"],
        }
    )
    assert analyzer._matches_filters("src/service.py") is True
    assert analyzer._matches_filters("legacy/CustomerService.java") is True
    assert analyzer._matches_filters("pkg/handler.go") is True
    assert analyzer._matches_filters("node_modules/pkg/index.js") is False
    assert analyzer._matches_filters("sub/vendor/lib.go") is False
    assert analyzer._matches_filters("tests/auth.test.py") is False
    assert analyzer._matches_filters("web/app.spec.js") is False
    assert analyzer._matches_filters("assets/logo.png") is False
    assert analyzer._matches_filters("docs/README.md") is False


def test_hotspot_detection_ranking_and_top_decile() -> None:
    now = datetime.now(UTC)
    changes: list[CommitChange] = []
    # 15 commits on hot.py, 10 commits on warm.py, 4 commits on cold.py
    for i in range(15):
        changes.append(CommitChange(f"h{i}", "a1", now, "src/hot.py", 20, 5))
    for i in range(10):
        changes.append(CommitChange(f"w{i}", "a1", now, "src/warm.py", 10, 2))
    for i in range(4):
        changes.append(CommitChange(f"c{i}", "a1", now, "src/cold.py", 5, 1))

    detector = HotspotDetector({"min_commits_for_hotspot": 10, "lookback_days": 180})
    hotspots = detector.detect(changes)

    assert len(hotspots) == 2
    assert hotspots[0].file_path == "src/hot.py"
    assert hotspots[0].rank == 1
    assert hotspots[0].change_count == 15
    assert hotspots[0].total_lines_changed == 375
    assert hotspots[0].change_frequency == pytest.approx(2.5)
    assert hotspots[0].is_top_decile is True
    assert hotspots[1].file_path == "src/warm.py"
    assert hotspots[1].rank == 2


def test_temporal_coupling_calculation_and_bulk_commit_skip() -> None:
    now = datetime.now(UTC)
    changes: list[CommitChange] = []
    # auth.py and user.py change together in 4 of 5 commits -> coeff = 4/5 = 0.8
    for i in range(4):
        changes.append(CommitChange(f"c{i}", "a1", now, "src/auth.py", 5, 2))
        changes.append(CommitChange(f"c{i}", "a1", now, "src/user.py", 3, 1))
    changes.append(CommitChange("c4", "a1", now, "src/auth.py", 2, 1))
    changes.append(CommitChange("c5", "a1", now, "src/user.py", 2, 1))

    # Bulk reformat commit touching 12 files (above max_files_per_commit=5) must be ignored
    for k in range(12):
        changes.append(CommitChange("bulk_fmt", "a1", now, f"src/fmt_{k}.py", 1, 1))

    analyzer = CouplingAnalyzer(
        {"coupling_threshold": 0.5, "min_co_changes": 2, "max_files_per_commit": 5}
    )
    couplings = analyzer.analyze(changes)

    assert len(couplings) == 1
    assert couplings[0].file1 == "src/auth.py"
    assert couplings[0].file2 == "src/user.py"
    assert couplings[0].co_change_count == 4
    assert couplings[0].coupling_coefficient == pytest.approx(0.8)


def test_code_age_and_stability_classification() -> None:
    now = datetime(2026, 11, 22, tzinfo=UTC)
    changes: list[CommitChange] = [
        # Stable legacy file: touched 600 days ago and 450 days ago
        CommitChange("old1", "a1", now - timedelta(days=600), "legacy/core.py", 100, 0),
        CommitChange("old2", "a1", now - timedelta(days=450), "legacy/core.py", 5, 2),
        # Active file: touched 20 days ago
        CommitChange("act1", "a1", now - timedelta(days=120), "src/api.py", 40, 0),
        CommitChange("act2", "a1", now - timedelta(days=20), "src/api.py", 10, 4),
    ]
    # Volatile file: 12 recent commits within last 25 days
    for i in range(12):
        changes.append(
            CommitChange(
                f"vol{i}",
                "a1",
                now - timedelta(days=25 - i * 2),
                "src/volatile.py",
                15,
                5,
            )
        )

    ages = AgeAnalyzer().analyze(changes, reference_time=now)
    assert ages["legacy/core.py"].stability == "stable_legacy"
    assert ages["legacy/core.py"].age_days == pytest.approx(600.0)
    assert ages["legacy/core.py"].days_since_last_change == pytest.approx(450.0)
    assert ages["src/api.py"].stability == "active"
    assert ages["src/volatile.py"].stability == "volatile"


def test_ownership_distribution_bus_factor_and_silo_detection() -> None:
    now = datetime.now(UTC)
    changes: list[CommitChange] = [
        # Single owner -> 100%, bus factor 1, silo True
        CommitChange("s1", "author_alice", now, "src/silo.py", 10, 0),
        CommitChange("s2", "author_alice", now, "src/silo.py", 10, 2),
        CommitChange("s3", "author_alice", now, "src/silo.py", 5, 1),
        # Collective ownership across 4 devs -> 25% each, bus factor 2, silo False
        CommitChange("t1", "author_a", now, "src/shared.py", 10, 0),
        CommitChange("t2", "author_b", now, "src/shared.py", 10, 0),
        CommitChange("t3", "author_c", now, "src/shared.py", 10, 0),
        CommitChange("t4", "author_d", now, "src/shared.py", 10, 0),
    ]

    own = OwnershipAnalyzer({"silo_threshold_pct": 80.0}).analyze(changes)
    assert own["src/silo.py"].primary_author == "author_alice"
    assert own["src/silo.py"].primary_ownership_pct == pytest.approx(100.0)
    assert own["src/silo.py"].bus_factor == 1
    assert own["src/silo.py"].is_knowledge_silo is True

    assert own["src/shared.py"].author_count == 4
    assert own["src/shared.py"].primary_ownership_pct == pytest.approx(25.0)
    assert own["src/shared.py"].bus_factor == 2
    assert own["src/shared.py"].is_knowledge_silo is False


async def test_empty_repository_handling(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "empty_repo")
    insights = await GitAnalyzer().analyze(repo)
    assert insights.total_commits == 0
    assert insights.hotspots == []
    assert insights.couplings == []
    assert insights.file_ages == {}
    assert insights.ownership == {}


async def test_single_commit_repository(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "single_commit")
    _commit_file(repo, "src/main.py", "print('hello')\n", "initial commit")

    insights = await GitAnalyzer({"min_commits_for_hotspot": 1}).analyze(repo)
    assert insights.total_commits == 1
    assert len(insights.hotspots) == 1
    assert insights.couplings == []
    assert "src/main.py" in insights.file_ages
    assert insights.ownership["src/main.py"].bus_factor == 1


def test_large_repository_10k_commits_performance() -> None:
    """Parsing and analyzing 10,000 commits must complete well under the 5-minute budget."""
    now_ts = int(datetime.now(UTC).timestamp())
    lines: list[str] = []
    for i in range(10_000):
        lines.append(f"===PVS_COMMIT==={i:040x}\x1fdev{i % 8}@example.org\x1f{now_ts - i * 60}")
        f1 = f"src/mod_{i % 25}.py"
        f2 = f"src/mod_{(i + 1) % 25}.py"
        lines.append(f"12\t4\t{f1}")
        lines.append(f"5\t1\t{f2}")
    payload = "\n".join(lines) + "\n"

    analyzer = GitAnalyzer({"lookback_days": 180, "min_commits_for_hotspot": 10})
    cutoff = datetime.now(UTC) - timedelta(days=180)

    t0 = time.monotonic()
    changes = analyzer._parse_numstat_log(payload, cutoff)
    insights = analyzer.analyze_changes(changes)
    elapsed = time.monotonic() - t0

    assert insights.total_commits == 10_000
    assert len(insights.hotspots) == 25
    assert elapsed < 10.0, f"10K commit analysis took too long: {elapsed:.2f}s"


async def test_binary_files_ignored_in_real_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "binary_repo")
    # Write genuine binary bytes (NUL bytes trigger git binary detection: "-\t-\t...")
    _commit_file(repo, "assets/image.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "add png")
    _commit_file(repo, "src/blob.py", b"\x00\x01\x02\x03\x00binary", "binary disguised as py")
    _commit_file(repo, "src/clean.py", "def ok():\n    return True\n", "add text py")

    analyzer = GitAnalyzer({"min_commits_for_hotspot": 1, "include_patterns": ["*"]})
    insights = await analyzer.analyze(repo)

    assert "src/clean.py" in insights.file_ages
    assert "assets/image.png" not in insights.file_ages
    assert "src/blob.py" not in insights.file_ages


async def test_merge_commits_skipped_in_real_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "merge_repo")
    _commit_file(repo, "src/base.py", "V = 1\n", "init")
    _git(repo, "checkout", "-b", "feature")
    _commit_file(repo, "src/feat.py", "F = 1\n", "feature work")
    _git(repo, "checkout", "main")
    _commit_file(repo, "src/main_only.py", "M = 1\n", "main work")
    _git(repo, "merge", "--no-ff", "feature", "-m", "merge feature branch")

    # 3 non-merge commits (init, feature work, main work); the 4th is a merge commit -> skipped
    insights = await GitAnalyzer({"min_commits_for_hotspot": 1}).analyze(repo)
    assert insights.total_commits == 3


async def test_cutoff_date_filtering(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "cutoff_repo")
    now = datetime.now(UTC)
    _commit_file(
        repo,
        "src/ancient.py",
        "OLD = 1\n",
        "old commit",
        commit_dt=now - timedelta(days=365),
    )
    _commit_file(
        repo,
        "src/recent.py",
        "NEW = 1\n",
        "recent commit",
        commit_dt=now - timedelta(days=5),
    )

    insights = await GitAnalyzer({"lookback_days": 90, "min_commits_for_hotspot": 1}).analyze(
        repo, reference_time=now
    )
    assert insights.total_commits == 1
    assert "src/recent.py" in insights.file_ages
    assert "src/ancient.py" not in insights.file_ages


async def test_author_email_anonymization_privacy(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "privacy_repo")
    secret_email = "alice.engineer@private-corp.example"
    _commit_file(repo, "src/pay.py", "A = 1\n", "c1", author_email=secret_email)
    _commit_file(repo, "src/pay.py", "A = 2\n", "c2", author_email=secret_email)

    insights = await GitAnalyzer({"anonymize_authors": True}).analyze(repo)
    serialized = str(insights.to_dict())
    assert secret_email not in serialized
    assert "private-corp" not in serialized
    expected_hash = anonymize_author(secret_email)
    assert insights.ownership["src/pay.py"].primary_author == expected_hash


def test_brownfield_yaml_config_and_zero_secrets() -> None:
    cfg_path = REPO_ROOT / "config" / "brownfield.yaml"
    assert cfg_path.exists()
    cfg = load_brownfield_config(cfg_path)
    assert cfg["enabled"] is True
    assert cfg["lookback_days"] == 180
    assert cfg["min_commits_for_hotspot"] == 10
    assert cfg["coupling_threshold"] == pytest.approx(0.5)
    assert cfg["max_commits_analyzed"] == 10000
    assert cfg["anonymize_authors"] is True

    detector = HybridSecretDetector()
    for rel in ("config/brownfield.yaml", "docs/git-history-analysis.md"):
        findings = detector.detect((REPO_ROOT / rel).read_text(encoding="utf-8"))
        assert findings == [], f"Unexpected secret findings in {rel}: {findings}"
