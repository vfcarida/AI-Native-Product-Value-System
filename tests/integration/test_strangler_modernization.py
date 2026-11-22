"""Integration tests for EVO-031 Strangler Fig modernization & compilable ACL execution."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from product_value_system.agents import BrownfieldAgent
from product_value_system.brownfield import ACLGenerator, Module

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str, env_extra: dict[str, str] | None = None) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Strangler Dev",
        "GIT_AUTHOR_EMAIL": "dev@example.org",
        "GIT_COMMITTER_NAME": "Strangler Dev",
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
    _git(path, "config", "user.name", "Strangler Dev")
    _git(path, "config", "user.email", "dev@example.org")
    return path


def _commit_files(
    repo: Path,
    files: dict[str, str],
    message: str,
    *,
    commit_dt: datetime | None = None,
) -> None:
    for rel, content in files.items():
        full = repo / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        _git(repo, "add", rel)
    env_extra: dict[str, str] = {}
    if commit_dt is not None:
        iso = commit_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        env_extra["GIT_AUTHOR_DATE"] = iso
        env_extra["GIT_COMMITTER_DATE"] = iso
    _git(repo, "commit", "-m", message, env_extra=env_extra)


async def test_end_to_end_strangler_planning_on_real_pvs_codebase() -> None:
    """Run full BrownfieldAgent (Git history + Strangler Fig) on the real PVS repository."""
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("Repository .git directory not present.")

    head_before = _git(REPO_ROOT, "rev-parse", "HEAD")
    agent = BrownfieldAgent(
        config={
            "brownfield": {
                "git_analysis": {
                    "enabled": True,
                    "lookback_days": 3650,
                    "min_commits_for_hotspot": 1,
                    "include_patterns": ["*.py"],
                    "exclude_patterns": ["tests/**"],
                    "anonymize_authors": True,
                },
                "strangler_fig": {
                    "enabled": True,
                    "top_candidates": 3,
                    "boundary_detection": {
                        "method": "static_analysis",
                        "min_module_size_lines": 500,
                        "max_module_size_lines": 10000,
                    },
                },
            }
        }
    )

    report = await agent.analyze_repository(REPO_ROOT)

    assert _git(REPO_ROOT, "rev-parse", "HEAD") == head_before
    assert len(report.strangler_plans) >= 1
    assert len(report.strangler_plans) <= 3
    for plan in report.strangler_plans:
        compile(plan.acl_template, f"<{plan.target_module.name}_acl>", "exec")
        assert len(plan.replacement_steps) == 8
        assert plan.total_estimated_effort_hours > 0

    md = report.to_markdown()
    assert "## Strangler Fig Modernization Plans" in md


def test_generated_acl_code_compiles_and_executes_canary_cutover(tmp_path: Path) -> None:
    """Generated ACL code compiles, routes via flag, enforces auth, and falls back on error."""
    src_file = tmp_path / "src" / "payments" / "gateway.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(
        "def charge(user_id, amount):\n"
        "    return {'charged': amount}\n\n"
        "def refund(tx_id):\n"
        "    return {'refunded': tx_id}\n",
        encoding="utf-8",
    )

    module = Module(
        name="payments",
        file_paths=["src/payments/gateway.py"],
        lines_of_code=800,
    )
    acl_source = ACLGenerator().generate(module, repo_path=tmp_path)

    namespace: dict[str, Any] = {}
    exec(compile(acl_source, "<payments_acl>", "exec"), namespace)
    acl_cls = namespace["PaymentsACL"]

    class _LegacyPayments:
        def charge(self, user_id: str, amount: float) -> dict[str, Any]:
            return {"engine": "legacy", "user_id": user_id, "amount": amount}

        def refund(self, tx_id: str) -> dict[str, Any]:
            return {"engine": "legacy", "tx_id": tx_id}

    class _ModernPayments:
        def __init__(self, should_fail: bool = False) -> None:
            self.should_fail = should_fail

        def charge(self, user_id: str, amount: float) -> dict[str, Any]:
            if self.should_fail:
                raise RuntimeError("Modern payment gateway timeout")
            return {"engine": "modern", "user_id": user_id, "amount": amount}

        def refund(self, tx_id: str) -> dict[str, Any]:
            return {"engine": "modern", "tx_id": tx_id}

    class _ToggleFlag:
        def __init__(self, enabled: bool) -> None:
            self.enabled = enabled

        def is_enabled(self, _name: str, user_id: str | None = None) -> bool:
            _ = user_id
            return self.enabled

    flag = _ToggleFlag(enabled=False)
    modern = _ModernPayments(should_fail=False)
    acl = acl_cls(_LegacyPayments(), modern, flag)

    # 1. Flag OFF -> routes to legacy
    assert acl.charge("u1", 50.0)["engine"] == "legacy"

    # 2. Flag ON -> routes to modern
    flag.enabled = True
    assert acl.charge("u1", 50.0)["engine"] == "modern"
    assert acl.refund("tx-99")["engine"] == "modern"

    # 3. Modern error -> automatic fallback to legacy (zero disruption)
    modern.should_fail = True
    assert acl.charge("u1", 50.0)["engine"] == "legacy"

    # 4. Security boundary check -> denies unauthorized operation
    guarded_acl = acl_cls(
        _LegacyPayments(),
        _ModernPayments(),
        flag,
        authorize_fn=lambda op, _payload: op != "refund",
    )
    with pytest.raises(PermissionError, match="ACL security boundary denied"):
        guarded_acl.refund("tx-blocked")


async def test_brownfield_agent_integrates_git_history_and_strangler_plans(
    tmp_path: Path,
) -> None:
    """BrownfieldAgent merges git history with Strangler Fig planning end-to-end."""
    repo = _init_repo(tmp_path / "strangler_repo")
    now = datetime.now(UTC)

    # High-churn, complex, decoupled payments module
    for i in range(5):
        _commit_files(
            repo,
            {
                "src/payments/processor.py": (
                    f"def charge(user_id, amount):\n"
                    f"    if amount > {i}:\n"
                    f"        for step in range(3):\n"
                    f"            if step and user_id:\n"
                    f"                return amount + step\n"
                    f"    elif amount == 0:\n"
                    f"        return 0\n"
                    f"    return -1\n\n"
                    f"def refund(tx_id):\n"
                    f"    return tx_id\n"
                )
            },
            f"update payments {i}",
            commit_dt=now - timedelta(days=10 - i),
        )

    # Low-churn helper module
    _commit_files(
        repo,
        {"src/helpers/formatters.py": "def fmt(x):\n    return str(x)\n"},
        "add helper",
        commit_dt=now - timedelta(days=30),
    )

    agent = BrownfieldAgent(
        config={
            "brownfield": {
                "git_analysis": {
                    "enabled": True,
                    "lookback_days": 180,
                    "min_commits_for_hotspot": 3,
                },
                "strangler_fig": {
                    "enabled": True,
                    "top_candidates": 2,
                    "boundary_detection": {
                        "method": "static_analysis",
                        "min_module_size_lines": 1,
                    },
                },
            }
        }
    )

    result = await agent.execute({"repo_path": str(repo)})
    assert result.success is True
    plans_data = result.output_artifact["strangler_plans"]
    assert len(plans_data) == 2
    assert plans_data[0]["target_module"]["name"] == "payments"
    assert "class PaymentsACL:" in plans_data[0]["acl_template"]
    assert "def charge(" in plans_data[0]["acl_template"]
    assert "def refund(" in plans_data[0]["acl_template"]


async def test_brownfield_agent_strangler_rollback_via_config(tmp_path: Path) -> None:
    """Setting strangler_fig.enabled: false cleanly disables Strangler planning."""
    repo = _init_repo(tmp_path / "strangler_off")
    _commit_files(repo, {"src/core/app.py": "def main():\n    return 1\n"}, "init")

    agent = BrownfieldAgent(
        config={
            "brownfield": {
                "git_analysis": {"enabled": True, "min_commits_for_hotspot": 1},
                "strangler_fig": {"enabled": False},
            }
        }
    )
    report = await agent.analyze_repository(repo)
    assert report.git_insights.total_commits == 1
    assert report.strangler_plans == []
