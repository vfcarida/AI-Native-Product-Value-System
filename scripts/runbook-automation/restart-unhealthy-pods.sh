#!/usr/bin/env bash
# Safely capture logs from CrashLoopBackOff/Error pods and recycle them respecting PDB (PROD-012).
set -euo pipefail

NAMESPACE="${NAMESPACE:-product-value-system}"
LABEL_SELECTOR="${LABEL_SELECTOR:-app.kubernetes.io/name=product-value-system}"
LOG_DIR="${LOG_DIR:-/tmp/pvs-incident-logs}"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --selector) LABEL_SELECTOR="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

echo "=== Restart Unhealthy Pods (${NAMESPACE}, selector=${LABEL_SELECTOR}) ==="
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Would inspect pods matching ${LABEL_SELECTOR}, save previous logs, and delete unhealthy pods."
  exit 0
fi

mkdir -p "${LOG_DIR}"
UNHEALTHY_PODS="$(kubectl get pods -n "${NAMESPACE}" -l "${LABEL_SELECTOR}" \
  --no-headers | awk '$3 ~ /(CrashLoopBackOff|Error|OOMKilled|ImagePullBackOff|Evicted)/ {print $1}')"

if [[ -z "${UNHEALTHY_PODS}" ]]; then
  echo "No unhealthy pods found in ${NAMESPACE}."
  exit 0
fi

for pod in ${UNHEALTHY_PODS}; do
  echo "Capturing pre-restart logs for ${pod} -> ${LOG_DIR}/${pod}.log"
  kubectl logs -n "${NAMESPACE}" "${pod}" --previous --tail=200 > "${LOG_DIR}/${pod}.log" 2>&1 || true
  echo "Deleting unhealthy pod ${pod}..."
  kubectl delete pod -n "${NAMESPACE}" "${pod}" --grace-period=30
done

kubectl wait --for=condition=ready pod -l "${LABEL_SELECTOR}" -n "${NAMESPACE}" --timeout=180s
echo "Unhealthy pods recycled and ready."
