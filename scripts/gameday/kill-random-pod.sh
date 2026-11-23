#!/usr/bin/env bash
# Chaos Engineering GameDay: Kill a single application pod via Chaos Mesh PodChaos (PROD-015).
set -euo pipefail

NAMESPACE="${NAMESPACE:-product-value-system-staging}"
APP_LABEL="${APP_LABEL:-product-value-system}"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-pvs-gameday-pod-kill}"
MODE="${MODE:-one}"
DURATION="${DURATION:-30s}"
ALLOW_PRODUCTION="false"
APPROVAL_TICKET=""
CLEANUP="false"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --allow-production) ALLOW_PRODUCTION="true"; shift ;;
    --approval-ticket) APPROVAL_TICKET="$2"; shift 2 ;;
    --cleanup) CLEANUP="true"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help)
      echo "Usage: $0 [--namespace <ns>] [--mode one] [--duration 30s] [--cleanup] [--dry-run]"
      exit 0
      ;;
    *) echo "ERROR: Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ "${NAMESPACE}" == "product-value-system" || "${NAMESPACE}" =~ (^|-)prod(uction)?($|-) ]]; then
  if [[ "${ALLOW_PRODUCTION}" != "true" || -z "${APPROVAL_TICKET}" ]]; then
    echo "BLOCKED: Refusing chaos injection in production namespace '${NAMESPACE}' without --allow-production and --approval-ticket." >&2
    exit 1
  fi
fi

if [[ "${CLEANUP}" == "true" ]]; then
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY-RUN] Would run: kubectl delete podchaos ${EXPERIMENT_NAME} -n ${NAMESPACE} --ignore-not-found"
    exit 0
  fi
  kubectl delete podchaos "${EXPERIMENT_NAME}" -n "${NAMESPACE}" --ignore-not-found
  echo "Cleaned up PodChaos ${EXPERIMENT_NAME} in ${NAMESPACE}."
  exit 0
fi

MANIFEST="$(cat <<EOF
apiVersion: chaos-mesh.org/v1alpha1
kind: PodChaos
metadata:
  name: ${EXPERIMENT_NAME}
  namespace: ${NAMESPACE}
spec:
  action: pod-kill
  mode: ${MODE}
  selector:
    namespaces:
      - ${NAMESPACE}
    labelSelectors:
      app.kubernetes.io/name: ${APP_LABEL}
  duration: "${DURATION}"
EOF
)"

echo "=== GameDay Random Pod Kill (${NAMESPACE}, mode=${MODE}, duration=${DURATION}) ==="
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Would apply Chaos Mesh PodChaos manifest:"
  echo "${MANIFEST}"
  exit 0
fi

echo "${MANIFEST}" | kubectl apply -f -
echo "Applied PodChaos ${EXPERIMENT_NAME}. Run '$0 --namespace ${NAMESPACE} --cleanup' to abort early."
