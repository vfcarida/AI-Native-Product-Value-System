"""Integration tests for EVO-030 brownfield Git history analysis & BrownfieldAgent."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from product_value_system.agents import BrownfieldAgent
from product_value_system.brownfield import GitAnalyzer

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str, env_extra: dict[str, str] | None = None) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Integration Dev",
        "GIT_AUTHOR_EMAIL": "dev@example.org",
        "GIT_COMMITTER_NAME": "Integration Dev",
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
    _git(path, "config", "user.name", "Integration Dev")
    _git(path, "config", "user.email", "dev@example.org")
    return path


def _commit_files(
    repo: Path,
    files: dict[str, str],
    message: str,
    *,
    author_email: str = "alice@example.org",
    commit_dt: datetime | None = None,
) -> None:
    for rel, content in files.items():
        full = repo / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        _git(repo, "add", rel)
    env_extra = {
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_EMAIL": author_email,
    }
    if commit_dt is not None:
        iso = commit_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        env_extra["GIT_AUTHOR_DATE"] = iso
        env_extra["GIT_COMMITTER_DATE"] = iso
    _git(repo, "commit", "-m", message, env_extra=env_extra)


async def test_analyze_real_pvs_git_repository() -> None:
    """Analyze the real Product Value System git repository without mutating it."""
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("Repository .git directory not present in this checkout.")

    head_before = _git(REPO_ROOT, "rev-parse", "HEAD")
    analyzer = GitAnalyzer(
        {
            "lookback_days": 3650,
            "min_commits_for_hotspot": 1,
            "coupling_threshold": 0.5,
            "include_patterns": ["*.py"],
            "exclude_patterns": ["tests/**"],
            "anonymize_authors": True,
        }
    )
    insights = await analyzer.analyze(REPO_ROOT)

    assert _git(REPO_ROOT, "rev-parse", "HEAD") == head_before
    assert insights.total_commits >= 1
    assert insights.analyzed_files >= 1
    # Privacy check: no '@' character inside any anonymized primary_author
    for own in insights.ownership.values():
        assert "@" not in own.primary_author
        assert own.primary_author.startswith("author_")


async def test_brownfield_agent_generates_modernization_report(tmp_path: Path) -> None:
    """End-to-end BrownfieldAgent merges git history and static complexity into a full report."""
    repo = _init_repo(tmp_path / "legacy_payments")
    now = datetime.now(UTC)

    # Coupled pair: auth.py + user.py changed together 4 times by different authors
    for i in range(4):
        _commit_files(
            repo,
            {
                "src/auth.py": f"def check(u):\n    if u:\n        return {i}\n    return -1\n",
                "src/user.py": f"def load(u):\n    return {{'id': u, 'rev': {i}}}\n",
            },
            f"update auth and user {i}",
            author_email=f"dev{i}@example.org",
            commit_dt=now - timedelta(days=10 - i),
        )

    # Single-author hotspot: legacy_billing.py changed 6 times
    for i in range(6):
        _commit_files(
            repo,
            {
                "src/legacy_billing.py": (
                    f"def bill(a, b, c):\n"
                    f"    if a > {i}:\n"
                    f"        for x in range(b):\n"
                    f"            if x and c:\n"
                    f"                return x\n"
                    f"    elif b > 0:\n"
                    f"        return b\n"
                    f"    return 0\n"
                )
            },
            f"patch billing {i}",
            author_email="solo.owner@example.org",
            commit_dt=now - timedelta(days=6 - i),
        )

    agent = BrownfieldAgent(
        config={
            "enabled": True,
            "lookback_days": 180,
            "min_commits_for_hotspot": 3,
            "coupling_threshold": 0.5,
            "min_co_changes": 2,
            "anonymize_authors": True,
        }
    )
    report = await agent.analyze_repository(repo)

    assert report.git_insights.total_commits == 10
    assert len(report.refactor_priorities) >= 2
    assert report.refactor_priorities[0].file_path == "src/legacy_billing.py"
    assert report.refactor_priorities[0].is_knowledge_silo is True
    assert any(
        {c.file1, c.file2} == {"src/auth.py", "src/user.py"} for c in report.coupling_risks
    )
    assert len(report.recommended_actions) >= 2

    markdown = report.to_markdown()
    assert "# Brownfield Modernization Report" in markdown
    assert "src/legacy_billing.py" in markdown
    assert "solo.owner@example.org" not in markdown

    # Also verify standard AgentResult execute() contract
    agent_res = await agent.execute({"repo_path": str(repo)})
    assert agent_res.success is True
    assert agent_res.output_artifact["summary"]["hotspots_found"] >= 2


async def test_verify_hotspots_and_complexity_priority_merge(tmp_path: Path) -> None:
    """High-churn + high-complexity file outranks both simple high-churn and untouched complex."""
    repo = _init_repo(tmp_path / "priority_merge")
    now = datetime.now(UTC)

    # 1. Untouched complex legacy file (changed once 400 days ago -> outside 180d hotspot window)
    complex_stable = "\n".join(
        ["def ancient(x):"] + [f"    if x == {k}: return {k}" for k in range(20)] + ["    return 0"]
    )
    _commit_files(
        repo,
        {"src/stable_complex.py": complex_stable},
        "ancient commit",
        commit_dt=now - timedelta(days=400),
    )

    # 2. High-churn SIMPLE file (5 commits, complexity=1)
    for i in range(5):
        _commit_files(
            repo,
            {"src/simple_hot.py": f"VERSION = '{i}'\n"},
            f"bump version {i}",
            commit_dt=now - timedelta(days=15 - i),
        )

    # 3. High-churn COMPLEX file (5 commits, high cyclomatic complexity)
    for i in range(5):
        complex_hot = (
            f"def route(a, b, c, d):\n"
            f"    if a == {i} and b:\n"
            f"        for k in range(10):\n"
            f"            if k > c or d:\n"
            f"                return k\n"
            f"    elif b:\n"
            f"        return b\n"
            f"    return -1\n"
        )
        _commit_files(
            repo,
            {"src/complex_hot.py": complex_hot},
            f"fix routing {i}",
            commit_dt=now - timedelta(days=10 - i),
        )

    agent = BrownfieldAgent(
        config={"lookback_days": 180, "min_commits_for_hotspot": 4, "anonymize_authors": True}
    )
    report = await agent.analyze_repository(repo)

    paths_ranked = [p.file_path for p in report.refactor_priorities]
    assert paths_ranked[0] == "src/complex_hot.py"
    assert paths_ranked[1] == "src/simple_hot.py"
    # Untouched ancient complex file is NOT ranked as a churn hotspot
    assert "src/stable_complex.py" not in paths_ranked
    assert (
        report.refactor_priorities[0].priority_score
        > report.refactor_priorities[1].priority_score
    )


async def test_brownfield_git_analysis_rollback_disabled(tmp_path: Path) -> None:
    """Setting enabled: false cleanly disables git analysis while preserving static scan."""
    repo = _init_repo(tmp_path / "disabled_git")
    _commit_files(
        repo,
        {"src/mod.py": "def fn(x):\n    if x:\n        return 1\n    return 0\n"},
        "c1",
    )

    agent = BrownfieldAgent(config={"enabled": False})
    report = await agent.analyze_repository(repo)

    assert report.git_insights.total_commits == 0
    assert report.refactor_priorities == []
    assert "src/mod.py" in report.static_complexity
    assert report.static_complexity["src/mod.py"].cyclomatic_complexity == 2
