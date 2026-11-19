#!/usr/bin/env python
"""
Pre-commit hook for secret scanning.

Usage:
    Install: ./scripts/install-git-hooks.sh
    Run manually: python scripts/pre-commit-secret-scan.py
"""

import subprocess
import sys
from pathlib import Path

# Add repository src to path
SCRIPT_REPO_ROOT = Path(__file__).resolve().parent.parent
if (SCRIPT_REPO_ROOT / "src").exists():
    sys.path.insert(0, str(SCRIPT_REPO_ROOT / "src"))
elif (Path.cwd() / "src").exists():
    sys.path.insert(0, str(Path.cwd() / "src"))

from product_value_system.security.secret_detection import SecretDetector  # noqa: E402


def get_staged_files(cwd: Path) -> list[str]:
    """Get list of staged files."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().split("\n") if result.stdout.strip() else []


def scan_file(file_path: Path, detector: SecretDetector) -> list[tuple[int, str, str]]:
    """
    Scan a file for secrets.

    Returns:
        List of (line_number, secret_type, preview) tuples
    """
    if not file_path.exists():
        return []

    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    secrets_found: list[tuple[int, str, str]] = []

    for line_num, line in enumerate(content.split("\n"), start=1):
        secrets = detector.find_secrets(line)
        for secret_value, secret_type in secrets:
            secrets_found.append((line_num, secret_type, secret_value[:20]))

    return secrets_found


def load_exemptions(repo_root: Path) -> set[str]:
    """
    Load exempted files from .secretignore.

    Returns:
        Set of exempted file patterns
    """
    ignore_file = repo_root / ".secretignore"

    if not ignore_file.exists():
        return set()

    exemptions: set[str] = set()

    for raw_line in ignore_file.read_text(encoding="utf-8").split("\n"):
        line = raw_line.split("#", 1)[0].strip()
        if line:
            exemptions.add(line)

    return exemptions


def is_exempted(file_path: str, exemptions: set[str]) -> bool:
    """Check if file is exempted from scanning."""
    normalized = file_path.replace("\\", "/")
    for pattern in exemptions:
        if pattern in normalized:
            return True
    return False


def main() -> int:
    """Main pre-commit hook logic."""
    repo_root = Path.cwd()
    detector = SecretDetector()
    exemptions = load_exemptions(repo_root)

    staged_files = get_staged_files(repo_root)

    if not staged_files:
        return 0

    secrets_found: dict[str, list[tuple[int, str, str]]] = {}

    for file_path_str in staged_files:
        if not file_path_str:
            continue

        if is_exempted(file_path_str, exemptions):
            continue

        file_path = repo_root / file_path_str
        secrets = scan_file(file_path, detector)

        if secrets:
            secrets_found[file_path_str] = secrets

    if secrets_found:
        print("COMMIT BLOCKED: Secrets detected in staged files")
        print()

        for file_path_str, secrets in secrets_found.items():
            print(f"  {file_path_str}:")
            for line_num, secret_type, secret_preview in secrets:
                print(f"    Line {line_num}: {secret_type} ({secret_preview}...)")

        print()
        print("Secret Management Best Practices:")
        print("  1. Use environment variables for secrets (not hardcoded)")
        print("  2. Store secrets in .env files (added to .gitignore)")
        print("  3. Use secret management tools (e.g., AWS Secrets Manager)")
        print()
        print("To exempt a file, add it to .secretignore (with justification)")
        print()

        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
