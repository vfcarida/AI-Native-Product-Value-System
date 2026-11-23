#!/usr/bin/env bash
# PostgreSQL Patroni Automatic Failover Test (< 30s RTO target) (PROD-011)
set -euo pipefail

NAMESPACE="${NAMESPACE:-product-value-system}"
APP_DEPLOYMENT="${APP_DEPLOYMENT:-product-value-system}"
MAX_FAILOVER_SECONDS="${MAX_FAILOVER_SECONDS:-30}"

echo "=== PostgreSQL Patroni Automatic Failover Test ==="

PRIMARY_POD="$(kubectl get pods -n "${NAMESPACE}" -l app=postgres-ha,role=master -o jsonpath='{.items[0].metadata.name}')"
if [[ -z "${PRIMARY_POD}" ]]; then
  echo "ERROR: No active postgres-ha master pod found in namespace ${NAMESPACE}" >&2
  exit 1
fi
echo "Current primary: ${PRIMARY_POD}"

echo "Simulating primary failure (${PRIMARY_POD})..."
START_TIME="$(date +%s)"
kubectl delete pod "${PRIMARY_POD}" -n "${NAMESPACE}" --force --grace-period=0

echo "Waiting for Patroni leader election..."
FAILOVER_TIME=0
while true; do
  NEW_PRIMARY="$(kubectl get pods -n "${NAMESPACE}" -l app=postgres-ha,role=master -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")"
  NOW="$(date +%s)"
  FAILOVER_TIME="$(( NOW - START_TIME ))"
  if [[ -n "${NEW_PRIMARY}" && "${NEW_PRIMARY}" != "${PRIMARY_POD}" ]]; then
    echo "Failover complete! New primary: ${NEW_PRIMARY} (${FAILOVER_TIME}s)"
    break
  fi
  if [[ "${FAILOVER_TIME}" -gt 60 ]]; then
    echo "ERROR: Failover timed out (>60s)" >&2
    exit 1
  fi
  sleep 1
done

echo "Verifying application readiness after failover..."
kubectl exec -n "${NAMESPACE}" "deploy/${APP_DEPLOYMENT}" -- \
  curl -fsS http://localhost:8000/health/ready >/dev/null

if [[ "${FAILOVER_TIME}" -gt "${MAX_FAILOVER_SECONDS}" ]]; then
  echo "FAIL: Failover took ${FAILOVER_TIME}s (target: < ${MAX_FAILOVER_SECONDS}s)" >&2
  exit 1
fi

echo "SUCCESS: Automatic failover completed in ${FAILOVER_TIME}s (< ${MAX_FAILOVER_SECONDS}s)."
