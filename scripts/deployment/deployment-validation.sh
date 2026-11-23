#!/usr/bin/env bash
# Post-deployment health & Prometheus SLO validation gate (PROD-014).
set -euo pipefail

TARGET_URL="${TARGET_URL:-http://localhost:8000}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://prometheus:9090}"
NAMESPACE="${NAMESPACE:-product-value-system}"
DEPLOYMENT="${DEPLOYMENT:-product-value-system}"
VALIDATION_WINDOW="${VALIDATION_WINDOW:-5m}"
MAX_ERROR_RATE="${MAX_ERROR_RATE:-0.02}"
MAX_LATENCY_P99="${MAX_LATENCY_P99:-2.0}"
MAX_RESTARTS="${MAX_RESTARTS:-3}"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-url) TARGET_URL="$2"; shift 2 ;;
    --prometheus-url) PROMETHEUS_URL="$2"; shift 2 ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --deployment) DEPLOYMENT="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help)
      echo "Usage: $0 [--target-url <url>] [--prometheus-url <url>] [--namespace <ns>] [--deployment <name>] [--dry-run]"
      exit 0
      ;;
    *) echo "ERROR: Unknown option: $1" >&2; exit 2 ;;
  esac
done

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

echo "=== Production Deployment Validation ==="
echo "Target:     ${TARGET_URL}"
echo "Prometheus: ${PROMETHEUS_URL}"
echo "Namespace:  ${NAMESPACE} (${DEPLOYMENT})"

if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Would validate error rate < ${MAX_ERROR_RATE}, p99 latency < ${MAX_LATENCY_P99}s, /health/ready == 200, pod readiness, and restart count < ${MAX_RESTARTS}."
  exit 0
fi

PYTHON_BIN="$(resolve_python)"

# Checks 1-3: Live readiness + Prometheus 5xx error rate & p99 latency
"${PYTHON_BIN}" - "${TARGET_URL%/}" "${PROMETHEUS_URL%/}" "${NAMESPACE}" "${VALIDATION_WINDOW}" "${MAX_ERROR_RATE}" "${MAX_LATENCY_P99}" <<'PYEOF'
import json
import sys
import urllib.parse
import urllib.request

target_url, prom_url, ns, window, max_err_s, max_lat_s = sys.argv[1:7]
max_err = float(max_err_s)
max_lat = float(max_lat_s)

# Check 1: Live /health/ready
with urllib.request.urlopen(f"{target_url}/health/ready", timeout=10) as resp:
    if resp.status != 200:
        raise SystemExit(f"FAIL: /health/ready returned HTTP {resp.status}")
    payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("status") != "ready":
        raise SystemExit(f"FAIL: /health/ready status={payload.get('status')}")
print("[PASS] Live /health/ready returned 200 (ready)")


def prom_scalar(query: str) -> float:
    url = f"{prom_url}/api/v1/query?{urllib.parse.urlencode({'query': query})}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    results = data.get("data", {}).get("result", [])
    if not results:
        return 0.0
    val = float(results[0]["value"][1])
    return 0.0 if val != val else val  # guard NaN


# Check 2: 5xx error rate
err_query = (
    f'sum(rate(http_requests_total{{code=~"5..",namespace="{ns}"}}[{window}]))'
    f'/clamp_min(sum(rate(http_requests_total{{namespace="{ns}"}}[{window}])), 1e-9)'
)
error_rate = prom_scalar(err_query)
print(f"5xx error rate: {error_rate * 100:.2f}% (threshold: < {max_err * 100:.2f}%)")
if error_rate >= max_err:
    raise SystemExit(f"FAIL: Error rate {error_rate * 100:.2f}% exceeds {max_err * 100:.2f}%")
print("[PASS] 5xx error rate within threshold")

# Check 3: p99 latency
lat_query = (
    f'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket'
    f'{{namespace="{ns}"}}[{window}])) by (le))'
)
latency_p99 = prom_scalar(lat_query)
print(f"Latency p99: {latency_p99:.3f}s (threshold: < {max_lat:.2f}s)")
if latency_p99 >= max_lat:
    raise SystemExit(f"FAIL: Latency p99 {latency_p99:.3f}s exceeds {max_lat:.2f}s")
print("[PASS] Latency p99 within threshold")
PYEOF

# Checks 4-5: Kubernetes pod readiness and restart counts (when kubectl available)
if command -v kubectl >/dev/null 2>&1; then
  TOTAL_PODS="$(kubectl get deployment "${DEPLOYMENT}" -n "${NAMESPACE}" -o jsonpath='{.spec.replicas}')"
  READY_PODS="$(kubectl get deployment "${DEPLOYMENT}" -n "${NAMESPACE}" -o jsonpath='{.status.readyReplicas}')"
  READY_PODS="${READY_PODS:-0}"
  echo "Ready pods: ${READY_PODS}/${TOTAL_PODS}"
  if [[ "${READY_PODS}" != "${TOTAL_PODS}" ]]; then
    echo "FAIL: Not all pods ready (${READY_PODS}/${TOTAL_PODS})" >&2
    exit 1
  fi
  echo "[PASS] All pods ready"

  RESTART_COUNT="$(kubectl get pods -n "${NAMESPACE}" -l "app.kubernetes.io/name=${DEPLOYMENT}" \
    -o jsonpath='{.items[*].status.containerStatuses[0].restartCount}' \
    | awk '{sum=0; for(i=1;i<=NF;i++) sum+=$i; print sum}')"
  echo "Total pod restarts: ${RESTART_COUNT} (threshold: < ${MAX_RESTARTS})"
  if [[ "${RESTART_COUNT}" -ge "${MAX_RESTARTS}" ]]; then
    echo "FAIL: Excessive pod restarts detected (${RESTART_COUNT} >= ${MAX_RESTARTS})" >&2
    exit 1
  fi
  echo "[PASS] Pod restart count within threshold"
else
  echo "INFO: kubectl not installed; skipped cluster pod readiness checks."
fi

echo "Deployment validation PASSED."
