#!/usr/bin/env bash
# Zero-Downtime Rolling Deployment Verification Test (0 failed requests) (PROD-011)
set -euo pipefail

NAMESPACE="${NAMESPACE:-product-value-system}"
DEPLOYMENT="${DEPLOYMENT:-product-value-system}"
SERVICE_URL="${SERVICE_URL:-http://product-value-system:8000/health/ready}"
DURATION_SECONDS="${DURATION_SECONDS:-120}"

echo "=== Zero-Downtime Rolling Deployment Test ==="

RESULTS_FILE="$(mktemp)"
trap 'rm -f "${RESULTS_FILE}"' EXIT

(
  for _ in $(seq 1 "${DURATION_SECONDS}"); do
    STATUS="$(curl -s -o /dev/null -w '%{http_code}' "${SERVICE_URL}" || echo '000')"
    echo "$(date +%s) ${STATUS}" >> "${RESULTS_FILE}"
    sleep 1
  done
) &
TRAFFIC_PID=$!

sleep 5
echo "Triggering rolling restart on deployment/${DEPLOYMENT}..."
kubectl rollout restart "deployment/${DEPLOYMENT}" -n "${NAMESPACE}"
kubectl rollout status "deployment/${DEPLOYMENT}" -n "${NAMESPACE}" --timeout=300s

wait "${TRAFFIC_PID}"

TOTAL_REQUESTS="$(wc -l < "${RESULTS_FILE}")"
SUCCESS_REQUESTS="$(grep -c ' 200$' "${RESULTS_FILE}" || true)"
FAILED_REQUESTS="$(( TOTAL_REQUESTS - SUCCESS_REQUESTS ))"

echo "Total requests:      ${TOTAL_REQUESTS}"
echo "Successful requests: ${SUCCESS_REQUESTS}"
echo "Failed requests:     ${FAILED_REQUESTS}"

if [[ "${FAILED_REQUESTS}" -gt 0 ]]; then
  echo "FAIL: ${FAILED_REQUESTS} requests failed during rolling deployment." >&2
  exit 1
fi

echo "SUCCESS: Zero-downtime deployment achieved (${SUCCESS_REQUESTS}/${TOTAL_REQUESTS} HTTP 200)."
