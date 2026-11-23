#!/usr/bin/env bash
# Auto-scale application pods during CPU/latency incidents, capped at HPA maxReplicas (PROD-012).
set -euo pipefail

NAMESPACE="${NAMESPACE:-product-value-system}"
DEPLOYMENT="${DEPLOYMENT:-product-value-system}"
SCALE_INCREMENT="${SCALE_INCREMENT:-2}"
MAX_REPLICAS_OVERRIDE="${MAX_REPLICAS_OVERRIDE:-}"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --deployment) DEPLOYMENT="$2"; shift 2 ;;
    --increment) SCALE_INCREMENT="$2"; shift 2 ;;
    --max-replicas) MAX_REPLICAS_OVERRIDE="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

echo "=== Auto-Scale Up (${NAMESPACE}/${DEPLOYMENT}, +${SCALE_INCREMENT}) ==="
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Would scale ${DEPLOYMENT} in ${NAMESPACE} by +${SCALE_INCREMENT} up to HPA maxReplicas (${MAX_REPLICAS_OVERRIDE:-cluster-default})."
  exit 0
fi

CURRENT="$(kubectl get deployment "${DEPLOYMENT}" -n "${NAMESPACE}" -o jsonpath='{.spec.replicas}')"
HPA_MAX="${MAX_REPLICAS_OVERRIDE:-$(kubectl get hpa "${DEPLOYMENT}" -n "${NAMESPACE}" -o jsonpath='{.spec.maxReplicas}' 2>/dev/null || echo '10')}"
NEW="$(( CURRENT + SCALE_INCREMENT ))"

if [[ "${NEW}" -gt "${HPA_MAX}" ]]; then
  echo "Capping target replicas from ${NEW} to HPA maxReplicas (${HPA_MAX})."
  NEW="${HPA_MAX}"
fi

kubectl scale deployment "${DEPLOYMENT}" --replicas="${NEW}" -n "${NAMESPACE}"
kubectl rollout status "deployment/${DEPLOYMENT}" -n "${NAMESPACE}" --timeout=300s

for attempt in 1 2 3 4 5; do
  if kubectl exec -n "${NAMESPACE}" "deploy/${DEPLOYMENT}" -- curl -fsS http://localhost:8000/health/ready >/dev/null; then
    echo "Scaling complete (${CURRENT} -> ${NEW}) and /health/ready verified."
    exit 0
  fi
  echo "Readiness check attempt ${attempt}/5 failed; retrying..."
  sleep 5
done

echo "ERROR: Scaled to ${NEW} but readiness check failed." >&2
exit 1
