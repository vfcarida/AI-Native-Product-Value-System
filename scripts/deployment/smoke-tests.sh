#!/usr/bin/env bash
# Post-deployment smoke test suite against live PVS health, readiness, metrics, and SLO endpoints (PROD-014).
set -euo pipefail

TARGET_URL="${TARGET_URL:-http://localhost:8000}"
API_TOKEN="${API_TOKEN:-}"
RESULTS_DIR="${RESULTS_DIR:-test-results}"
MAX_LATENCY_SECONDS="${MAX_LATENCY_SECONDS:-2.0}"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-url) TARGET_URL="$2"; shift 2 ;;
    --results-dir) RESULTS_DIR="$2"; shift 2 ;;
    --max-latency) MAX_LATENCY_SECONDS="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help)
      echo "Usage: $0 [--target-url <url>] [--results-dir <dir>] [--max-latency <sec>] [--dry-run]"
      exit 0
      ;;
    *) echo "ERROR: Unknown option: $1" >&2; exit 2 ;;
  esac
done

TARGET_URL="${TARGET_URL%/}"

resolve_python() {
  local candidate
  for candidate in "${PYTHON_BIN:-}" python3 python; do
    if [[ -n "${candidate}" ]] && command -v "${candidate}" >/dev/null 2>&1; then
      if "${candidate}" -c "import sys; raise SystemExit(0)" >/dev/null 2>&1; then
        echo "${candidate}"
        return 0
      fi
    fi
  done
  echo "ERROR: No working python3/python interpreter found." >&2
  return 1
}

echo "=== Post-Deployment Smoke Tests ==="
echo "Target: ${TARGET_URL}"

if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Would run 6 smoke checks (/health/live, /health/startup, /health/ready + DB, /metrics, /slos, /health/metrics-summary latency < ${MAX_LATENCY_SECONDS}s) against ${TARGET_URL}."
  exit 0
fi

PYTHON_BIN="$(resolve_python)"

mkdir -p "${RESULTS_DIR}"
RESULTS_FILE="${RESULTS_DIR}/smoke-tests.json"

"${PYTHON_BIN}" - "${TARGET_URL}" "${API_TOKEN}" "${MAX_LATENCY_SECONDS}" "${RESULTS_FILE}" <<'PYEOF'
import json
import sys
import time
import urllib.error
import urllib.request

target_url, api_token, max_latency_str, results_file = sys.argv[1:5]
max_latency = float(max_latency_str)
results = []


def fetch(path: str, *, auth: bool = False):
    headers = {"Accept": "application/json, text/plain"}
    if auth and api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    req = urllib.request.Request(f"{target_url}{path}", headers=headers, method="GET")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            elapsed = time.perf_counter() - start
            return resp.status, body, elapsed
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        elapsed = time.perf_counter() - start
        return exc.code, body, elapsed
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return 0, str(exc), elapsed


def record(name: str, passed: bool, details: str = "") -> None:
    status = "pass" if passed else "fail"
    results.append({"test": name, "status": status, "details": details})
    marker = "PASS" if passed else "FAIL"
    suffix = f" - {details}" if details else ""
    print(f"[{marker}] {name}{suffix}")


# Test 1: Liveness probe
code, body, _ = fetch("/health/live")
try:
    live_ok = code == 200 and json.loads(body).get("status") == "healthy"
except json.JSONDecodeError:
    live_ok = False
record("liveness_probe", live_ok, f"HTTP {code}")

# Test 2: Startup probe
code, body, _ = fetch("/health/startup")
try:
    startup_ok = code == 200 and json.loads(body).get("status") == "started"
except json.JSONDecodeError:
    startup_ok = False
record("startup_probe", startup_ok, f"HTTP {code}")

# Test 3: Readiness & Database connectivity
code, body, _ = fetch("/health/ready")
db_status = "unknown"
ready_ok = False
if code == 200:
    try:
        payload = json.loads(body)
        db_check = payload.get("checks", {}).get("database", {})
        db_status = db_check.get("status", "") if isinstance(db_check, dict) else str(db_check)
        ready_ok = payload.get("status") == "ready" and db_status in {"ok", "ready", "healthy"}
    except json.JSONDecodeError:
        ready_ok = False
record("readiness_and_database", ready_ok, f"HTTP {code}, database={db_status}")

# Test 4: Prometheus metrics endpoint (with optional Bearer auth)
code, body, _ = fetch("/metrics", auth=True)
metrics_ok = code == 200 and ("pvs_" in body or "# HELP" in body or "# TYPE" in body)
record("metrics_endpoint", metrics_ok, f"HTTP {code}")

# Test 5: SLO evaluation endpoint
code, body, _ = fetch("/slos", auth=True)
try:
    slo_ok = code == 200 and "slos" in json.loads(body)
except json.JSONDecodeError:
    slo_ok = False
record("slo_endpoint", slo_ok, f"HTTP {code}")

# Test 6: API response latency & metrics summary
code, body, elapsed = fetch("/health/metrics-summary", auth=True)
latency_ok = code == 200 and elapsed < max_latency
record("api_latency", latency_ok, f"HTTP {code}, {elapsed:.3f}s (limit < {max_latency:.2f}s)")

passed_count = sum(1 for r in results if r["status"] == "pass")
failed_count = sum(1 for r in results if r["status"] == "fail")
summary = {
    "target_url": target_url,
    "total": len(results),
    "passed": passed_count,
    "failed": failed_count,
    "results": results,
}
with open(results_file, "w", encoding="utf-8") as fh:
    json.dump(summary, fh, indent=2)

print(f"\nSmoke Test Summary: {passed_count}/{len(results)} passed, {failed_count} failed")
sys.exit(1 if failed_count > 0 else 0)
PYEOF
