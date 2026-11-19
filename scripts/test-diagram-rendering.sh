#!/usr/bin/env bash
#
# Test that all Mermaid diagrams render without errors.
#
# Requires: mermaid-cli (mmdc)
# Install: npm install -g @mermaid-js/mermaid-cli

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIAGRAMS_DIR="$SCRIPT_DIR/../docs/architecture/diagrams"

echo "Testing Mermaid diagram rendering..."

if ! command -v mmdc &> /dev/null; then
    echo "Mermaid CLI not installed (optional for rendering)"
    echo "Install: npm install -g @mermaid-js/mermaid-cli"
    exit 0
fi

errors=0

for diagram in "$DIAGRAMS_DIR"/*.mmd; do
    echo "Testing: $(basename "$diagram")"

    if mmdc -i "$diagram" -o /tmp/test-diagram.svg 2>/dev/null; then
        echo "  Valid"
    else
        echo "  Invalid syntax"
        errors=$((errors + 1))
    fi
done

if [ $errors -eq 0 ]; then
    echo ""
    echo "All diagrams valid"
    exit 0
else
    echo ""
    echo "$errors diagram(s) invalid"
    exit 1
fi
