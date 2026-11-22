"""Unit tests verifying EVO-033 Makefile structure, targets, .env.example, and CI wiring."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import yaml

from product_value_system.security.hybrid_detector import HybridSecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE_PATH = REPO_ROOT / "Makefile"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

REQUIRED_TARGETS = {
    "help",
    "install",
    "dev-setup",
    "test",
    "test-unit",
    "test-integration",
    "test-coverage",
    "lint",
    "format",
    "type-check",
    "validate-policies",
    "security-check",
    "clean",
    "demo",
    "demo-greenfield",
    "demo-brownfield",
    "demo-custom",
    "docker-check",
    "env-check",
    "docker-build",
    "docker-build-sandbox",
    "docker-run",
    "platform-info",
}


def _parse_documented_targets(text: str) -> dict[str, str]:
    pattern = re.compile(r"^([a-zA-Z0-9_-]+):.*?##\s+(.+)$")
    found: dict[str, str] = {}
    for line in text.splitlines():
        m = pattern.match(line)
        if m:
            found[m.group(1)] = m.group(2).strip()
    return found


def test_makefile_exists_and_uses_valid_tab_indentation() -> None:
    assert MAKEFILE_PATH.exists()
    content = MAKEFILE_PATH.read_text(encoding="utf-8")

    # Verify default goal and cross-platform Python/OS detection
    assert ".DEFAULT_GOAL := help" in content
    assert "command -v python3" in content
    assert "uname -s" in content

    # Verify no recipe lines accidentally use leading spaces instead of literal TABs
    lines = content.splitlines()
    in_recipe = False
    for lineno, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            if not line.strip():
                in_recipe = False
            continue
        if re.match(r"^[a-zA-Z0-9_.-]+:", line) and not line.startswith("."):
            in_recipe = True
            continue
        if line.startswith((".", "ifeq", "else", "endif", "ifndef", "ifdef")) or ":=" in line:
            in_recipe = False
            continue
        if in_recipe:
            assert line.startswith("\t"), (
                f"Makefile line {lineno} inside recipe must start with a TAB, got: {line!r}"
            )


def test_makefile_declares_all_required_documented_targets_and_phony() -> None:
    content = MAKEFILE_PATH.read_text(encoding="utf-8")
    documented = _parse_documented_targets(content)

    missing = REQUIRED_TARGETS - set(documented.keys())
    assert missing == set(), f"Missing documented Makefile targets: {sorted(missing)}"

    # Join continued .PHONY lines and verify every documented target is declared .PHONY
    unfolded = content.replace("\\\n", " ")
    phony_match = re.search(r"^\.PHONY:\s*(.+)$", unfolded, re.MULTILINE)
    assert phony_match is not None
    phony_targets = set(phony_match.group(1).split())
    missing_phony = REQUIRED_TARGETS - phony_targets
    assert missing_phony == set(), f"Targets missing from .PHONY: {sorted(missing_phony)}"


def test_makefile_demo_targets_reference_real_example_files() -> None:
    content = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "examples/greenfield/problem_payment_flow.yaml" in content
    assert "examples/brownfield/problem_document_legacy.yaml" in content
    assert (REPO_ROOT / "examples/greenfield/problem_payment_flow.yaml").exists()
    assert (REPO_ROOT / "examples/brownfield/problem_document_legacy.yaml").exists()


def test_env_example_and_gitignore_protection() -> None:
    assert ENV_EXAMPLE_PATH.exists()
    env_text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=" in env_text
    assert "ANTHROPIC_API_KEY=" in env_text
    assert "SANDBOX_PROVIDER=" in env_text
    assert "LOG_LEVEL=" in env_text

    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert any(line.strip() == ".env" for line in gitignore.splitlines())

    # Verify zero secret findings in .env.example and Makefile
    detector = HybridSecretDetector()
    for rel in (".env.example", "Makefile"):
        findings = detector.detect((REPO_ROOT / rel).read_text(encoding="utf-8"))
        assert findings == [], f"Unexpected secret findings in {rel}: {findings}"


def test_ci_workflow_and_readme_use_makefile_commands() -> None:
    ci_data = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(ci_data, dict)
    ci_text = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "make install" in ci_text
    assert "make lint" in ci_text
    assert "make test-coverage" in ci_text
    assert "make clean" in ci_text

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "### Using Make (Recommended)" in readme
    assert "make help" in readme
    assert "make demo" in readme
    assert "make dev-setup" in readme

    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "make dev-setup" in contributing
    assert "make help" in contributing


def test_makefile_help_awk_renders_all_required_targets() -> None:
    awk_bin = shutil.which("awk")
    if awk_bin is None:
        git_awk = Path(r"C:\Program Files\Git\usr\bin\awk.exe")
        if git_awk.exists():
            awk_bin = str(git_awk)
    if awk_bin is None:
        return

    proc = subprocess.run(
        [
            awk_bin,
            "--posix",
            'BEGIN {FS = ":[^#]*## "} /^[a-zA-Z0-9_-]+:[^#]*## / {printf "%s|%s\\n", $1, $2}',
            str(MAKEFILE_PATH),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    rendered = {
        line.split("|", 1)[0]: line.split("|", 1)[1]
        for line in proc.stdout.splitlines()
        if "|" in line
    }
    assert REQUIRED_TARGETS <= set(rendered.keys())
    assert "Show this help message" in rendered["help"]
