#!/usr/bin/env bash
# Chaos Engineering GameDay: Inject controlled network latency via Chaos Mesh NetworkChaos (PROD-015).
set -euo pipefail

NAMESPACE="${NAMESPACE:-product-value-system-staging}"
APP_LABEL="${APP_LABEL:-product-value-system}"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-pvs-gameday-network-latency}"
LATENCY="${LATENCY:-250ms}"
JITTER="${JITTER:-50ms}"
DURATION="${DURATION:-5m}"
ALLOW_PRODUCTION="false"
APPROVAL_TICKET=""
CLEANUP="false"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --latency) LATENCY="$2"; shift 2 ;;
    --jitter) JITTER="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --allow-production) ALLOW_PRODUCTION="true"; shift ;;
    --approval-ticket) APPROVAL_TICKET="$2"; shift 2 ;;
    --cleanup) CLEANUP="true"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help)
      echo "Usage: $0 [--namespace <ns>] [--latency 250ms] [--jitter 50ms] [--duration 5m] [--cleanup] [--dry-run]"
      exit 0
      ;;
    *) echo "ERROR: Unknown option: $1" >&2; exit 2 ;;
  esac
done

# Production safety guardrail (blocks production namespaces unless explicitly approved)
if [[ "${NAMESPACE}" == "product-value-system" || "${NAMESPACE}" =~ (^|-)prod(uction)?($|-) ]]; then
  if [[ "${ALLOW_PRODUCTION}" != "true" || -z "${APPROVAL_TICKET}" ]]; then
    echo "BLOCKED: Refusing chaos injection in production namespace '${NAMESPACE}' without --allow-production and --approval-ticket." >&2
    exit 1
  fi
fi

if [[ "${CLEANUP}" == "true" ]]; then
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY-RUN] Would run: kubectl delete networkchaos ${EXPERIMENT_NAME} -n ${NAMESPACE} --ignore-not-found"
    exit 0
  fi
  kubectl delete networkchaos "${EXPERIMENT_NAME}" -n "${NAMESPACE}" --ignore-not-found
  echo "Cleaned up NetworkChaos ${EXPERIMENT_NAME} in ${NAMESPACE}."
  exit 0
fi

MANIFEST="$(cat <<EOF
apiVersion: chaos-mesh.org/v1alpha1
kind: NetworkChaos
metadata:
  name: ${EXPERIMENT_NAME}
  namespace: ${NAMESPACE}
spec:
  action: delay
  mode: one
  selector:
    namespaces:
      - ${NAMESPACE}
    labelSelectors:
      app.kubernetes.io/name: ${APP_LABEL}
  delay:
    latency: "${LATENCY}"
    jitter: "${JITTER}"
    correlation: "25"
  duration: "${DURATION}"
EOF
)"

echo "=== GameDay Network Latency Injection (${NAMESPACE}, ${LATENCY} +/- ${JITTER}, ${DURATION}) ==="
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Would apply Chaos Mesh NetworkChaos manifest:"
  echo "${MANIFEST}"
  exit 0
fi

echo "${MANIFEST}" | kubectl apply -f -
echo "Applied NetworkChaos ${EXPERIMENT_NAME}. Run '$0 --namespace ${NAMESPACE} --cleanup' to abort early."
