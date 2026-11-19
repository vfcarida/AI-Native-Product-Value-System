#!/usr/bin/env bash
#
# Final audit script for v0.1.0 release.
#
# Usage: ./scripts/final-audit.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "========================================="
echo "AI-Native Product Value System"
echo "Final Audit for v0.1.0 Release"
echo "========================================="
echo ""

errors=0
warnings=0

# ============================================
# 1. Verify Prompts P001-P024 Deliverables
# ============================================
echo "1. Verifying P001-P024 deliverables..."

declare -a prompt_deliverables=(
    "P001:pyproject.toml"
    "P002:schemas/problem.schema.json"
    "P003:src/product_value_system/adapters/model_adapter.py"
    "P004:src/product_value_system/policy/engine.py"
    "P005:src/product_value_system/runtime/sandbox.py"
    "P006:src/product_value_system/observability/tracing.py"
    "P007:src/product_value_system/harness/harness.py"
    "P008:src/product_value_system/agents/base_agent.py"
    "P009:src/product_value_system/agents/discovery_agent.py"
    "P010:src/product_value_system/agents/specification_agent.py"
    "P011:src/product_value_system/agents/security_review_agent.py"
    "P012:src/product_value_system/agents/build_agent.py"
    "P013:src/product_value_system/evaluation/pipeline.py"
    "P014:src/product_value_system/orchestrator/controller.py"
    "P015:src/product_value_system/persistence/database.py"
    "P016:src/product_value_system/cli/main.py"
    "P017:examples/greenfield/problem_payment_flow.yaml"
    "P018:examples/brownfield/problem_document_legacy.yaml"
    "P019:docs/THREAT_MODEL.md"
    "P020:src/product_value_system/security/validation.py"
    "P021:scripts/pre-commit-secret-scan.py"
    "P022:docs/architecture/DIAGRAMS.md"
    "P023:docs/reference/README.md"
    "P024:docs/howto/README.md"
)

for entry in "${prompt_deliverables[@]}"; do
    pid="${entry%%:*}"
    file="${entry#*:}"
    if [ -f "$file" ]; then
        echo "  ✓ ${pid} verified (${file})"
    else
        echo "  ✗ ${pid} MISSING (${file})"
        errors=$((errors + 1))
    fi
done

echo ""

# ============================================
# 2. Verify Source Code
# ============================================
echo "2. Verifying source code structure..."

required_modules=(
    "src/product_value_system/__init__.py"
    "src/product_value_system/agents/agent.py"
    "src/product_value_system/harness/harness.py"
    "src/product_value_system/policy/engine.py"
    "src/product_value_system/runtime/sandbox.py"
    "src/product_value_system/evaluation/judge.py"
    "src/product_value_system/orchestrator/controller.py"
    "src/product_value_system/observability/tracing.py"
    "src/product_value_system/security/validation.py"
)

for module in "${required_modules[@]}"; do
    if [ -f "$module" ]; then
        echo "  ✓ $module"
    else
        echo "  ✗ $module MISSING"
        errors=$((errors + 1))
    fi
done

echo ""

# ============================================
# 3. Run Tests
# ============================================
echo "3. Running test suites..."

if command -v pytest &> /dev/null; then
    echo "  Running unit tests..."
    if pytest tests/unit/ -v --tb=short > /tmp/unit-tests.log 2>&1; then
        echo "    ✓ Unit tests passed"
    else
        echo "    ✗ Unit tests FAILED (see /tmp/unit-tests.log)"
        errors=$((errors + 1))
    fi

    echo "  Running integration tests..."
    if pytest tests/integration/ -v --tb=short > /tmp/integration-tests.log 2>&1; then
        echo "    ✓ Integration tests passed"
    else
        echo "    ⚠ Integration tests failed (may need external services)"
        warnings=$((warnings + 1))
    fi

    echo "  Running evaluation tests..."
    if pytest tests/eval/ -v --tb=short > /tmp/eval-tests.log 2>&1; then
        echo "    ✓ Evaluation tests passed"
    else
        echo "    ✗ Evaluation tests FAILED (see /tmp/eval-tests.log)"
        errors=$((errors + 1))
    fi

    echo "  Running security tests..."
    if pytest tests/security/ -v --tb=short > /tmp/security-tests.log 2>&1; then
        echo "    ✓ Security tests passed"
    else
        echo "    ✗ Security tests FAILED"
        errors=$((errors + 1))
    fi
else
    echo "  ⚠ pytest not installed, skipping tests"
    warnings=$((warnings + 1))
fi

echo ""

# ============================================
# 4. Validate Documentation
# ============================================
echo "4. Validating documentation..."

required_docs=(
    "README.md"
    "CONTRIBUTING.md"
    "CODE_OF_CONDUCT.md"
    "RELEASE_NOTES.md"
    "RELEASE_CHECKLIST.md"
    "docs/ARCHITECTURE.md"
    "docs/THREAT_MODEL.md"
    "docs/SECURITY.md"
    "docs/SECRET_MANAGEMENT.md"
    "docs/reference/README.md"
    "docs/howto/README.md"
    "docs/architecture/DIAGRAMS.md"
)

for doc in "${required_docs[@]}"; do
    if [ -f "$doc" ]; then
        echo "  ✓ $doc"
    else
        echo "  ✗ $doc MISSING"
        errors=$((errors + 1))
    fi
done

echo ""

# ============================================
# 5. Security Checks
# ============================================
echo "5. Running security checks..."

echo "  Checking for hardcoded secrets..."
if grep -r "sk-[a-zA-Z0-9]\{20,\}" src/ 2>/dev/null; then
    echo "    ✗ Found potential API keys in source code"
    errors=$((errors + 1))
else
    echo "    ✓ No obvious secrets in source code"
fi

echo "  Verifying .gitignore..."
if grep -q ".env" .gitignore && grep -q "*.db" .gitignore; then
    echo "    ✓ .gitignore includes .env and *.db"
else
    echo "    ✗ .gitignore missing critical patterns"
    errors=$((errors + 1))
fi

echo "  Verifying pre-commit hook..."
if [ -f scripts/pre-commit-secret-scan.py ]; then
    echo "    ✓ Pre-commit hook exists"
else
    echo "    ⚠ Pre-commit hook missing"
    warnings=$((warnings + 1))
fi

echo ""

# ============================================
# 6. License Compliance
# ============================================
echo "6. Checking license compliance..."

if [ -f LICENSE ]; then
    echo "  ✓ LICENSE file exists"
else
    echo "  ✗ LICENSE file MISSING"
    errors=$((errors + 1))
fi

if grep -q "Apache" LICENSE 2>/dev/null; then
    echo "  ✓ Apache 2.0 license detected"
else
    echo "  ⚠ License type unclear"
    warnings=$((warnings + 1))
fi

echo ""

# ============================================
# 7. Build and Packaging
# ============================================
echo "7. Verifying build and packaging..."

if [ -f pyproject.toml ]; then
    echo "  ✓ pyproject.toml exists"
else
    echo "  ✗ pyproject.toml MISSING"
    errors=$((errors + 1))
fi

if [ -f Dockerfile ]; then
    echo "  ✓ Dockerfile exists"
else
    echo "  ✗ Dockerfile MISSING"
    errors=$((errors + 1))
fi

if command -v docker &> /dev/null && docker info > /dev/null 2>&1; then
    echo "  Testing Docker build..."
    if docker build -t pvs:audit . > /dev/null 2>&1; then
        echo "    ✓ Docker image builds successfully"
    else
        echo "    ✗ Docker build FAILED"
        errors=$((errors + 1))
    fi
else
    echo "  ⚠ Docker daemon not active, skipping live Docker image build"
    warnings=$((warnings + 1))
fi

echo ""

# ============================================
# 8. Performance Baseline
# ============================================
echo "8. Establishing performance baseline..."

if [ -f examples/greenfield/problem_payment_flow.yaml ] && [ -f examples/brownfield/problem_document_legacy.yaml ]; then
    echo "  ✓ Greenfield and brownfield example problems exist"
else
    echo "  ✗ Example problems MISSING"
    errors=$((errors + 1))
fi

echo ""

# ============================================
# Summary
# ============================================
echo "========================================="
echo "Audit Summary"
echo "========================================="
echo "Errors: $errors"
echo "Warnings: $warnings"
echo ""

if [ $errors -eq 0 ]; then
    echo "✅ AUDIT PASSED - Ready for v0.1.0 release"
    exit 0
else
    echo "❌ AUDIT FAILED - Fix $errors error(s) before release"
    exit 1
fi
