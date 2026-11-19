#!/usr/bin/env bash
#
# Test fresh installation from scratch.
#
# Usage: ./scripts/test-fresh-install.sh

set -euo pipefail

echo "========================================="
echo "Fresh Installation Test"
echo "========================================="
echo ""

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Create temporary directory
TEST_DIR=$(mktemp -d)
echo "Test directory: $TEST_DIR"

# Clone repository from local checkout (or remote URL if provided)
CLONE_SOURCE="${1:-$REPO_ROOT}"
echo "1. Cloning repository from $CLONE_SOURCE..."
git clone "$CLONE_SOURCE" "$TEST_DIR/ai-native-product-value-system"
cd "$TEST_DIR/ai-native-product-value-system"

# Check Python version
echo ""
echo "2. Checking Python version..."
PYTHON_BIN="python3"
if ! command -v "$PYTHON_BIN" &> /dev/null; then
    PYTHON_BIN="python"
fi
"$PYTHON_BIN" --version

if ! "$PYTHON_BIN" -c "import sys; exit(0 if sys.version_info >= (3, 12) else 1)"; then
    echo "✗ Python 3.12+ required"
    exit 1
fi

# Install dependencies
echo ""
echo "3. Installing dependencies..."
"$PYTHON_BIN" -m pip install -e ".[dev]"

# Run unit tests
echo ""
echo "4. Running unit tests..."
"$PYTHON_BIN" -m pytest tests/unit/ -v

# Optional Docker image build if daemon is running
if command -v docker &> /dev/null && docker info > /dev/null 2>&1; then
    echo ""
    echo "5. Building Docker image..."
    docker build -t pvs:test .

    echo ""
    echo "6. Verifying Docker image..."
    docker run --rm pvs:test id
else
    echo ""
    echo "5. Skipping Docker build (Docker daemon not active)"
fi

# Success
echo ""
echo "✅ Fresh installation successful!"
echo ""
echo "Cleanup: rm -rf $TEST_DIR"
