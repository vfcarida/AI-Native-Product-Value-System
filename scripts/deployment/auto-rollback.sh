#!/usr/bin/env bash
# Automatic rollback of a failed Kubernetes / Helm deployment with post-rollback health verification (PROD-014).
set -euo pipefail

NAMESPACE="${NAMESPACE:-product-value-system}"
DEPLOYMENT="${DEPLOYMENT:-product-value-system}"
HELM_RELEASE="${HELM_RELEASE:-}"
TO_REVISION="${TO_REVISION:-}"
WAIT_SECONDS="${WAIT_SECONDS:-15}"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --deployment) DEPLOYMENT="$2"; shift 2 ;;
    --helm-release) HELM_RELEASE="$2"; shift 2 ;;
    --to-revision) TO_REVISION="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help)
      echo "Usage: $0 [--namespace <ns>] [--deployment <name>] [--helm-release <rel>] [--to-revision <rev>] [--dry-run]"
      exit 0
      ;;
    *) echo "ERROR: Unknown option: $1" >&2; exit 2 ;;
  esac
done

echo "=== Automatic Deployment Rollback ==="
echo "Namespace:  ${NAMESPACE}"
echo "Deployment: ${DEPLOYMENT}"

if [[ "${DRY_RUN}" == "true" ]]; then
  if [[ -n "${HELM_RELEASE}" ]]; then
    echo "[DRY-RUN] Would run: helm rollback ${HELM_RELEASE} ${TO_REVISION} -n ${NAMESPACE} --wait --timeout 5m"
  else
    echo "[DRY-RUN] Would run: kubectl rollout undo deployment/${DEPLOYMENT} -n ${NAMESPACE} ${TO_REVISION:+--to-revision=${TO_REVISION}}"
  fi
  echo "[DRY-RUN] Would wait for rollout status and verify readyReplicas == spec.replicas and /health/ready."
  exit 0
fi

if [[ -n "${HELM_RELEASE}" ]]; then
  echo "Fetching Helm release history for ${HELM_RELEASE}..."
  helm history "${HELM_RELEASE}" -n "${NAMESPACE}"
  if [[ -n "${TO_REVISION}" ]]; then
    helm rollback "${HELM_RELEASE}" "${TO_REVISION}" -n "${NAMESPACE}" --wait --timeout 5m
  else
    helm rollback "${HELM_RELEASE}" -n "${NAMESPACE}" --wait --timeout 5m
  fi
else
  echo "Fetching Kubernetes rollout history..."
  kubectl rollout history "deployment/${DEPLOYMENT}" -n "${NAMESPACE}"
  if [[ -n "${TO_REVISION}" ]]; then
    kubectl rollout undo "deployment/${DEPLOYMENT}" -n "${NAMESPACE}" --to-revision="${TO_REVISION}"
  else
    kubectl rollout undo "deployment/${DEPLOYMENT}" -n "${NAMESPACE}"
  fi
fi

echo "Waiting for rollback rollout to stabilize..."
kubectl rollout status "deployment/${DEPLOYMENT}" -n "${NAMESPACE}" --timeout=5m
sleep "${WAIT_SECONDS}"

TOTAL_PODS="$(kubectl get deployment "${DEPLOYMENT}" -n "${NAMESPACE}" -o jsonpath='{.spec.replicas}')"
READY_PODS="$(kubectl get deployment "${DEPLOYMENT}" -n "${NAMESPACE}" -o jsonpath='{.status.readyReplicas}')"
READY_PODS="${READY_PODS:-0}"

if [[ "${READY_PODS}" != "${TOTAL_PODS}" ]]; then
  echo "ERROR: Rollback completed but pods not ready (${READY_PODS}/${TOTAL_PODS}). Manual intervention required!" >&2
  exit 1
fi

kubectl exec -n "${NAMESPACE}" "deploy/${DEPLOYMENT}" -- curl -fsS http://localhost:8000/health/ready >/dev/null
echo "Rollback complete and verified (${READY_PODS}/${TOTAL_PODS} pods ready, /health/ready 200 OK)."
