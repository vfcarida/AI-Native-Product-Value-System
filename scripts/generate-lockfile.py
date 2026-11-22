#!/usr/bin/env python3
"""
Deterministic dependency lockfile generator & parity verifier (v1.1 Supply-Chain Hygiene).

Reads all core dependencies declared in ``pyproject.toml``, resolves exact installed versions
via ``importlib.metadata``, and generates/verifies a sorted ``requirements.lock`` pinned with
``==`` specifiers for reproducible builds and ``pip-audit`` scanning.

Usage:
    python scripts/generate-lockfile.py          # Regenerate requirements.lock
    python scripts/generate-lockfile.py --check  # Verify parity with pyproject.toml (CI gate)
"""

from __future__ import annotations

import argparse
import importlib.metadata
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
LOCKFILE_PATH = REPO_ROOT / "requirements.lock"

_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#]+)")


def _normalize_pkg(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _extract_declared_packages(pyproject_path: Path) -> list[str]:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    raw_reqs: list[str] = list(project.get("dependencies", []))
    optional = project.get("optional-dependencies", {})
    for group_name, entries in optional.items():
        if group_name == "all":
            continue
        raw_reqs.extend(entries)

    names: set[str] = set()
    for req in raw_reqs:
        # Strip environment markers (`; sys_platform == ...`)
        base = req.split(";", 1)[0].strip()
        m = _REQ_NAME_RE.match(base)
        if m:
            norm = _normalize_pkg(m.group(1))
            if norm != "product-value-system":
                names.add(norm)
    return sorted(names)


def _extract_core_packages(pyproject_path: Path) -> list[str]:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    raw_reqs: list[str] = list(data.get("project", {}).get("dependencies", []))
    names: list[str] = []
    for req in raw_reqs:
        base = req.split(";", 1)[0].strip()
        m = _REQ_NAME_RE.match(base)
        if m:
            names.append(_normalize_pkg(m.group(1)))
    return sorted(set(names))


def generate_lockfile(
    pyproject_path: Path = PYPROJECT_PATH,
    lockfile_path: Path = LOCKFILE_PATH,
) -> list[str]:
    """Resolve installed versions for declared packages and write ``requirements.lock``."""
    declared = _extract_declared_packages(pyproject_path)
    pinned: list[str] = []
    for pkg in declared:
        try:
            ver = importlib.metadata.version(pkg)
            pinned.append(f"{pkg}=={ver}")
        except importlib.metadata.PackageNotFoundError:
            # Optional extras not installed in the current environment (e.g., cedarpy/asyncpg)
            continue

    header = [
        "# Auto-generated deterministic lockfile (python scripts/generate-lockfile.py)",
        "# Core and installed optional dependencies pinned with exact == versions.",
        "# Verify parity in CI with: python scripts/generate-lockfile.py --check",
    ]
    lockfile_path.write_text("\n".join([*header, *pinned]) + "\n", encoding="utf-8")
    return pinned


def check_lockfile(
    pyproject_path: Path = PYPROJECT_PATH,
    lockfile_path: Path = LOCKFILE_PATH,
) -> tuple[bool, list[str]]:
    """Verify ``requirements.lock`` exists, uses exact ``==`` pins, and covers all core deps."""
    errors: list[str] = []
    if not lockfile_path.exists():
        return False, [f"Lockfile missing: {lockfile_path}"]

    pinned_map: dict[str, str] = {}
    for lineno, raw in enumerate(lockfile_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _PIN_RE.match(line)
        if not m:
            errors.append(f"Line {lineno} is not strictly pinned with '==': {line!r}")
            continue
        pinned_map[_normalize_pkg(m.group(1))] = m.group(2)

    core_deps = _extract_core_packages(pyproject_path)
    missing = [pkg for pkg in core_deps if pkg not in pinned_map]
    if missing:
        errors.append(f"Core pyproject.toml dependencies missing from lockfile: {missing}")

    return len(errors) == 0, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify requirements.lock")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify lockfile format and core dependency coverage without modifying it",
    )
    args = parser.parse_args()

    if args.check:
        ok, errors = check_lockfile()
        if not ok:
            for err in errors:
                print(f"[FAIL] {err}", file=sys.stderr)
            return 1
        print("[OK] requirements.lock is strictly pinned and covers all core dependencies.")
        return 0

    pinned = generate_lockfile()
    print(f"[OK] Wrote {len(pinned)} pinned dependencies to {LOCKFILE_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
