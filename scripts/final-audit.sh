#!/usr/bin/env bash
#
# Full Production Readiness & Release Audit Script (EVO-001 through EVO-033).
#
# Usage: ./scripts/final-audit.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "=================================================="
echo "AI-Native Product Value System — Full Release Audit"
echo "Scope: P001-P024 + EVO-001..EVO-033 + Quality Gates"
echo "=================================================="
echo ""

errors=0

check_file() {
    local label="$1"
    local file="$2"
    if [ -f "$file" ]; then
        echo "  [OK] ${label} (${file})"
    else
        echo "  [FAIL] ${label} MISSING (${file})"
        errors=$((errors + 1))
    fi
}

echo "1. Verifying core & evolution deliverables (P001-P024 + EVO-001..EVO-033)..."
check_file "Core Packaging" "pyproject.toml"
check_file "JSON Schemas" "schemas/problem.schema.json"
check_file "Config Contracts" "src/product_value_system/contracts/config_schemas.py"
check_file "Cedar Policy Engine" "src/product_value_system/policy/engine.py"
check_file "Resilience & Circuit Breaker" "src/product_value_system/harness/resilience.py"
check_file "Parallel Orchestrator" "src/product_value_system/orchestrator/parallel_executor.py"
check_file "Token Caching (EVO-025)" "src/product_value_system/caching/prompt_cache.py"
check_file "Model Routing (EVO-026)" "src/product_value_system/routing/model_router.py"
check_file "Batch Evaluations (EVO-027)" "src/product_value_system/evaluation/batch_runner.py"
check_file "Egress Filtering (EVO-029)" "src/product_value_system/sandbox/network_filter.py"
check_file "Git History Analysis (EVO-030)" "src/product_value_system/brownfield/git_analyzer.py"
check_file "Strangler Fig Planner (EVO-031)" "src/product_value_system/brownfield/strangler_planner.py"
check_file "DevX SPACE/DORA (EVO-032)" "src/product_value_system/metrics/devx_tracker.py"
check_file "Makefile UX (EVO-033)" "Makefile"
check_file "Structured Logging & Diagnostics" "src/product_value_system/observability/structured_logging.py"
check_file "Empirical Benchmarks" "docs/BENCHMARKS.md"
echo ""

echo "2. Running system diagnostics (pvs doctor)..."
if python -m product_value_system.cli.main doctor --repo-root "$REPO_ROOT"; then
    echo "  [OK] pvs doctor healthy"
else
    echo "  [FAIL] pvs doctor reported errors"
    errors=$((errors + 1))
fi
echo ""

echo "3. Verifying Cedar/Python policy sync..."
if python scripts/validate-policy-sync.py; then
    echo "  [OK] Policy sync 100%"
else
    echo "  [FAIL] Policy sync mismatch"
    errors=$((errors + 1))
fi
echo ""

echo "4. Running static quality gates (ruff + mypy)..."
if ruff check src/ tests/ scripts/; then
    echo "  [OK] ruff clean"
else
    echo "  [FAIL] ruff errors"
    errors=$((errors + 1))
fi

if mypy src/; then
    echo "  [OK] mypy clean"
else
    echo "  [FAIL] mypy errors"
    errors=$((errors + 1))
fi
echo ""

echo "5. Running Hybrid ML secret scan on src/ and config/..."
if python -m product_value_system.security.detect_secrets src/ config/; then
    echo "  [OK] Zero secrets detected"
else
    echo "  [FAIL] Secret detector reported findings"
    errors=$((errors + 1))
fi
echo ""

echo "6. Running non-live pytest suite..."
if pytest -m "not integration_real" -q; then
    echo "  [OK] All non-live tests passed"
else
    echo "  [FAIL] Test failures detected"
    errors=$((errors + 1))
fi
echo ""

echo "=================================================="
if [ "$errors" -eq 0 ]; then
    echo "AUDIT PASSED: 0 errors across all release gates."
    exit 0
else
    echo "AUDIT FAILED: ${errors} gate(s) failed."
    exit 1
fi
