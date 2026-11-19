#!/usr/bin/env bash
#
# Install Git hooks for secret detection.
#
# Usage: ./scripts/install-git-hooks.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"

echo "Installing Git hooks..."

mkdir -p "$HOOKS_DIR"

ln -sf "../../scripts/pre-commit-secret-scan.py" "$HOOKS_DIR/pre-commit"
chmod +x "$HOOKS_DIR/pre-commit" || true
chmod +x "$REPO_ROOT/scripts/pre-commit-secret-scan.py" || true

echo "Pre-commit hook installed: $HOOKS_DIR/pre-commit"

if [ ! -f "$REPO_ROOT/.secretignore" ]; then
    cat > "$REPO_ROOT/.secretignore" <<'EOF'
# .secretignore - Exempt files from secret scanning
#
# Use this file to exempt false positives or test files.
# Always include a justification comment.

# Test fixtures and security test suites (contain synthetic example secrets)
tests/fixtures/example_secrets.txt  # Example secrets for testing only
tests/security/  # Security unit tests contain synthetic test keys

# Documentation (may reference secret patterns)
docs/SECURITY.md  # Security documentation references patterns
docs/THREAT_MODEL.md  # Threat model references example patterns
docs/SECRET_MANAGEMENT.md  # Secret management guide

# Schema examples
schemas/examples/  # Schema examples may have UUIDs/hashes
EOF
    echo "Created .secretignore template"
fi

echo ""
echo "Git hooks installed successfully!"
