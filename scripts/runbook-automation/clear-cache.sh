#!/usr/bin/env bash
# Safely flush stale prompt cache entries in Redis during cache corruption/staleness incidents (PROD-012).
set -euo pipefail

NAMESPACE="${NAMESPACE:-product-value-system}"
REDIS_POD="${REDIS_POD:-redis-0}"
KEY_PREFIX="${KEY_PREFIX:-pvs:cache:*}"
AUTO_CONFIRM="false"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --redis-pod) REDIS_POD="$2"; shift 2 ;;
    --prefix|--pattern) KEY_PREFIX="$2"; shift 2 ;;
    --yes) AUTO_CONFIRM="true"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

echo "=== Prompt Cache Clear (${NAMESPACE}/${REDIS_POD}, prefix=${KEY_PREFIX}) ==="
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Would scan and UNLINK keys matching '${KEY_PREFIX}' in ${REDIS_POD}."
  exit 0
fi

if [[ "${AUTO_CONFIRM}" != "true" ]]; then
  read -r -p "Clear cache keys matching '${KEY_PREFIX}' in ${NAMESPACE}/${REDIS_POD}? (yes/no): " CONFIRM
  if [[ "${CONFIRM}" != "yes" ]]; then
    echo "Cancelled."
    exit 0
  fi
fi

# Non-blocking UNLINK avoids Redis main-thread stalls caused by FLUSHALL/DEL
DELETED="$(kubectl exec -n "${NAMESPACE}" "${REDIS_POD}" -- sh -c \
  "redis-cli --scan --pattern '${KEY_PREFIX}' | xargs -r redis-cli UNLINK | awk '{s+=\$1} END {print s+0}'")"
echo "Unlinked ${DELETED} cache key(s)."
kubectl exec -n "${NAMESPACE}" "${REDIS_POD}" -- redis-cli PING
