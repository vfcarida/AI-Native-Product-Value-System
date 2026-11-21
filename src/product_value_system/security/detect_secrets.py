"""
CLI module for pre-commit (fast regex) and CI (hybrid ML + regex) secret scanning (EVO-024).

Usage:
    python -m product_value_system.security.detect_secrets --regex-only file1.py file2.py
    python -m product_value_system.security.detect_secrets --hybrid --output report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from product_value_system.security.hybrid_detector import HybridSecretDetector

_DEFAULT_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
}
_BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".db",
    ".sqlite",
    ".pyc",
    ".whl",
    ".ico",
    ".woff",
    ".woff2",
}


def load_secretignore(repo_root: Path) -> set[str]:
    """Load exempted path patterns from ``.secretignore``."""
    ignore_file = repo_root / ".secretignore"
    if not ignore_file.exists():
        return set()
    exemptions: set[str] = set()
    for raw in ignore_file.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            exemptions.add(line)
    return exemptions


def _is_exempt(path: Path, exemptions: set[str]) -> bool:
    normalized = str(path).replace("\\", "/")
    return any(pattern in normalized for pattern in exemptions)


def _iter_target_files(paths: list[Path], exemptions: set[str]) -> list[Path]:
    targets: list[Path] = []
    for item in paths:
        if item.is_file():
            if not _is_exempt(item, exemptions) and item.suffix.lower() not in _BINARY_EXTENSIONS:
                targets.append(item)
        elif item.is_dir():
            for child in sorted(item.rglob("*")):
                if not child.is_file():
                    continue
                if any(part in _DEFAULT_SKIP_DIRS for part in child.parts):
                    continue
                if child.suffix.lower() in _BINARY_EXTENSIONS:
                    continue
                if _is_exempt(child, exemptions):
                    continue
                targets.append(child)
    return targets


def scan_paths(
    paths: list[Path],
    detector: HybridSecretDetector,
    *,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Scan files/directories and return structured finding records."""
    root = repo_root or Path.cwd()
    exemptions = load_secretignore(root)
    files = _iter_target_files(paths, exemptions)
    results: list[dict[str, Any]] = []

    for file_path in files:
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        findings = detector.detect(content)
        for finding in findings:
            line_no = content.count("\n", 0, finding.start) + 1
            results.append(
                {
                    "file": str(file_path),
                    "line": line_no,
                    **finding.to_dict(),
                }
            )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m product_value_system.security.detect_secrets",
        description="Hybrid ML + regex secret scanner (EVO-024)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--regex-only",
        action="store_true",
        default=False,
        help="Fast regex-only scan (recommended for pre-commit hooks)",
    )
    mode.add_argument(
        "--hybrid",
        action="store_true",
        default=False,
        help="Comprehensive regex + ML contextual + entropy scan (recommended for CI)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Confidence threshold for hybrid ML detection (default: 0.7)",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=None,
        help="Optional YAML allowlist file (e.g., config/secret_detection_allowlist.yaml)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional YAML config file (e.g., config/secret_detection.yaml)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON report output file path",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan (defaults to current directory)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    use_ml = not args.regex_only
    if args.config is not None and args.config.exists():
        detector = HybridSecretDetector.from_config(args.config)
        if args.regex_only:
            detector.use_ml = False
        elif args.hybrid:
            detector.use_ml = True
    else:
        default_allow = Path("config/secret_detection_allowlist.yaml")
        allow_path = args.allowlist or (default_allow if default_allow.exists() else None)
        detector = HybridSecretDetector(
            use_ml=use_ml,
            confidence_threshold=args.threshold,
            allowlist_path=allow_path,
        )

    targets = args.paths if args.paths else [Path(".")]
    findings = scan_paths(targets, detector)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if findings:
            args.output.write_text(json.dumps(findings, indent=2), encoding="utf-8")
        else:
            # Empty file when clean matches CI `[ -s secrets-report.json ]` check
            args.output.write_text("", encoding="utf-8")

    if findings:
        print(f"Secrets detected ({len(findings)} finding(s)):")
        for item in findings:
            print(
                f"  {item['file']}:{item['line']} [{item['type']}] "
                f"(confidence={item['confidence']:.2f}, method={item['detection_method']})"
            )
        return 1

    print("No secrets detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
