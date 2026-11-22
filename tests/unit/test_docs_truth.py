"""Executable documentation & truth-in-advertising tests.

Prevents documentation drift by executing the README Python SDK snippet,
parsing every documented ``pvs`` CLI invocation against ``create_parser()``,
verifying top-level package exports, checking zero-warning CLI boot under
``python -W error``, and validating relative Markdown file links.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import product_value_system
from product_value_system.cli.main import create_parser

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_top_level_all_exports_resolve_cleanly() -> None:
    """Every symbol listed in product_value_system.__all__ must import without error."""
    assert product_value_system.__all__, "__all__ must not be empty"
    for name in product_value_system.__all__:
        obj = getattr(product_value_system, name)
        assert obj is not None, f"Export {name!r} resolved to None"


def test_readme_python_sdk_snippet_executes_live(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Extract and execute Python fenced blocks from README.md against repo root."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```python\s*\n(.*?)\n```", readme, flags=re.DOTALL)
    assert blocks, "Expected at least one ```python block in README.md"

    monkeypatch.chdir(REPO_ROOT)
    for idx, code in enumerate(blocks):
        namespace: dict[str, object] = {}
        exec(compile(code, f"README.md#python-block-{idx}", "exec"), namespace)  # noqa: S102

    stdout = capsys.readouterr().out
    assert "Routed ->" in stdout


def test_cli_reference_invocations_parse_cleanly() -> None:
    """Every documented `pvs ...` command in docs/reference/cli/commands.md must parse."""
    doc = (REPO_ROOT / "docs" / "reference" / "cli" / "commands.md").read_text(encoding="utf-8")
    parser = create_parser()

    # Collect commands from ```bash blocks and inline `pvs ...` table cells
    candidates: list[str] = []
    for block in re.findall(r"```bash\s*\n(.*?)\n```", doc, flags=re.DOTALL):
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("pvs "):
                candidates.append(line)

    # Match the final "Verified Invocation" column of Markdown tables
    for inline in re.findall(r"\|\s*`(pvs [^`]+)`\s*\|$", doc, flags=re.MULTILINE):
        candidates.append(inline.strip())

    assert len(candidates) >= 18, f"Expected >= 18 CLI invocations, found {len(candidates)}"

    for raw_cmd in candidates:
        # Remove optional bracket syntax `[--flag val]` -> `--flag val`
        normalized = raw_cmd.replace("[", "").replace("]", "")
        # Replace angle-bracket placeholders with concrete valid values
        normalized = normalized.replace("<problem|outcome|specification|evidence>", "problem")
        normalized = re.sub(r"<[^>]+>", "placeholder.yaml", normalized)
        tokens = shlex.split(normalized)
        assert tokens[0] == "pvs"
        parsed = parser.parse_args(tokens[1:])
        assert parsed.command, f"Command failed to bind subcommand: {raw_cmd}"


def test_python_module_cli_zero_warnings() -> None:
    """Running `python -W error -m product_value_system doctor` must emit zero warnings."""
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(REPO_ROOT / "src"))
    proc = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-m",
            "product_value_system",
            "doctor",
            "--repo-root",
            str(REPO_ROOT),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert "HEALTHY" in proc.stdout


def test_markdown_relative_links_exist() -> None:
    """All local relative Markdown links across README, docs/, CONTRIBUTING, RELEASE_NOTES exist."""
    md_files = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "CONTRIBUTING.md",
        REPO_ROOT / "RELEASE_NOTES.md",
        *sorted((REPO_ROOT / "docs").rglob("*.md")),
    ]
    link_re = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    broken: list[str] = []

    for md_path in md_files:
        text = md_path.read_text(encoding="utf-8")
        # Strip fenced code blocks to ignore example links inside code samples
        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        for target in link_re.findall(text):
            target = target.strip().split()[0]
            if (
                not target
                or target.startswith(("#", "http://", "https://", "mailto:", "file://"))
            ):
                continue
            rel_path = target.split("#", 1)[0]
            if not rel_path:
                continue
            resolved = (md_path.parent / rel_path).resolve()
            if not resolved.exists():
                broken.append(f"{md_path.relative_to(REPO_ROOT)} -> {target}")

    assert not broken, "Broken relative Markdown links found:\n" + "\n".join(broken)
