#!/usr/bin/env bash
# Chaos Engineering GameDay: Inject controlled memory pressure via Chaos Mesh StressChaos (PROD-015).
set -euo pipefail

NAMESPACE="${NAMESPACE:-product-value-system-staging}"
APP_LABEL="${APP_LABEL:-product-value-system}"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-pvs-gameday-memory-stress}"
WORKERS="${WORKERS:-2}"
MEMORY_SIZE="${MEMORY_SIZE:-512MB}"
DURATION="${DURATION:-3m}"
ALLOW_PRODUCTION="false"
APPROVAL_TICKET=""
CLEANUP="false"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --size) MEMORY_SIZE="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --allow-production) ALLOW_PRODUCTION="true"; shift ;;
    --approval-ticket) APPROVAL_TICKET="$2"; shift 2 ;;
    --cleanup) CLEANUP="true"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help)
      echo "Usage: $0 [--namespace <ns>] [--workers 2] [--size 512MB] [--duration 3m] [--cleanup] [--dry-run]"
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
    echo "[DRY-RUN] Would run: kubectl delete stresschaos ${EXPERIMENT_NAME} -n ${NAMESPACE} --ignore-not-found"
    exit 0
  fi
  kubectl delete stresschaos "${EXPERIMENT_NAME}" -n "${NAMESPACE}" --ignore-not-found
  echo "Cleaned up StressChaos ${EXPERIMENT_NAME} in ${NAMESPACE}."
  exit 0
fi

MANIFEST="$(cat <<EOF
apiVersion: chaos-mesh.org/v1alpha1
kind: StressChaos
metadata:
  name: ${EXPERIMENT_NAME}
  namespace: ${NAMESPACE}
spec:
  mode: one
  selector:
    namespaces:
      - ${NAMESPACE}
    labelSelectors:
      app.kubernetes.io/name: ${APP_LABEL}
  stressors:
    memory:
      workers: ${WORKERS}
      size: "${MEMORY_SIZE}"
  duration: "${DURATION}"
EOF
)"

echo "=== GameDay Memory Exhaustion (${NAMESPACE}, ${WORKERS} workers x ${MEMORY_SIZE}, ${DURATION}) ==="
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Would apply Chaos Mesh StressChaos manifest:"
  echo "${MANIFEST}"
  exit 0
fi

echo "${MANIFEST}" | kubectl apply -f -
echo "Applied StressChaos ${EXPERIMENT_NAME}. Run '$0 --namespace ${NAMESPACE} --cleanup' to abort early."
