"""Unit tests for modular Phase-2 & diagnostics CLI commands (Round 13)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_value_system.cli.main import main

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pvs_doctor_json_and_unhealthy_exit_code(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rc_ok = main(["doctor", "--repo-root", str(REPO_ROOT), "--json"])
    assert rc_ok == 0
    data = json.loads(capsys.readouterr().out)
    assert data["healthy"] is True
    assert len(data["checks"]) >= 5

    rc_bad = main(["doctor", "--repo-root", str(tmp_path), "--json"])
    assert rc_bad == 6
    bad_data = json.loads(capsys.readouterr().out)
    assert bad_data["healthy"] is False


def test_pvs_route_json_and_human_output(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "route",
            "--prompt",
            "Convert this CSV list into JSON.",
            "--task-type",
            "format_conversion",
            "--config",
            str(REPO_ROOT / "config" / "routing.yaml"),
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["complexity_level"] == "low"
    assert payload["model"]

    rc_text = main(["route", "--prompt", "Design distributed multi-region saga ledger."])
    assert rc_text == 0
    out_text = capsys.readouterr().out
    assert "Complexity Tier" in out_text


def test_pvs_devx_json_enforces_aggregate_privacy(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["devx", "--config", str(REPO_ROOT / "config" / "metrics.yaml"), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["aggregate_only"] is True
    assert payload["anonymize"] is True
    assert "space_scores" in payload
    assert "dora_metrics" in payload


def test_pvs_brownfield_json_with_strangler_on_temp_repo(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    import subprocess

    repo = tmp_path / "mini_repo"
    (repo / "billing").mkdir(parents=True)
    (repo / "billing" / "invoice.py").write_text("def total(x):\n    return x * 2\n", "utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "dev@example.org"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial billing module"], cwd=repo, check=True)

    rc = main(["brownfield", "--repo", str(repo), "--days", "30", "--strangler", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["commits_analyzed"] == 1
    assert len(payload["strangler_modules"]) >= 1
