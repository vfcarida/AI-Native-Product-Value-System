#!/usr/bin/env python3
"""
Scan text from stdin for secrets.

Usage:
    cat file.txt | python scripts/scan-text.py
    git log --format=%B | python scripts/scan-text.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from product_value_system.security.secret_detection import SecretDetector  # noqa: E402


def main() -> int:
    """Scan stdin for secrets."""
    detector = SecretDetector()

    text = sys.stdin.read()
    secrets = detector.find_secrets(text)

    if secrets:
        print("Secrets detected:", file=sys.stderr)
        for secret_value, secret_type in secrets:
            print(f"  {secret_type}: {secret_value[:20]}...", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
