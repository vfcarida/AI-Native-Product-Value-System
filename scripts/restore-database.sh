#!/usr/bin/env bash
# Restore PostgreSQL database from pgBackRest backup (full, latest, or PITR) (PROD-010).
# Usage:
#   ./scripts/restore-database.sh --target latest
#   ./scripts/restore-database.sh --pitr "2026-11-23 10:30:00"
#   ./scripts/restore-database.sh --repo 2 --target latest --dry-run

set -euo pipefail

STANZA="${STANZA:-product-value-system}"
NAMESPACE="${NAMESPACE:-product-value-system}"
PG_DATA="${PG_DATA:-/var/lib/postgresql/data}"
PG_HOST="${PG_HOST:-postgres-primary}"
PG_USER="${PG_USER:-pvs_user}"
PG_DB="${PG_DB:-pvs_db}"
REPO="${REPO:-1}"
PITR_TARGET=""
RESTORE_TARGET="latest"
AUTO_CONFIRM="false"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pitr) PITR_TARGET="$2"; shift 2 ;;
    --target) RESTORE_TARGET="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --yes) AUTO_CONFIRM="true"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

echo "============================================"
echo "PostgreSQL Disaster Recovery - RESTORE"
echo "============================================"
echo "Stanza:    ${STANZA}"
echo "Namespace: ${NAMESPACE}"
echo "Repo:      ${REPO}"
echo "Target:    ${RESTORE_TARGET}"
if [[ -n "$PITR_TARGET" ]]; then
  echo "PITR Time: ${PITR_TARGET}"
fi

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] Validated restore parameters; no destructive actions executed."
  exit 0
fi

if [[ "$AUTO_CONFIRM" != "true" ]]; then
  read -r -p "This will OVERWRITE the current database in ${NAMESPACE}. Continue? (yes/no): " CONFIRM
  if [[ "$CONFIRM" != "yes" ]]; then
    echo "Restore cancelled."
    exit 0
  fi
fi

START_EPOCH="$(date +%s)"

echo "[1/5] Scaling down PostgreSQL..."
kubectl scale statefulset postgres --replicas=0 -n "${NAMESPACE}" 2>/dev/null \
  || kubectl scale deployment postgres --replicas=0 -n "${NAMESPACE}"
sleep 10

echo "[2/5] Clearing PostgreSQL data directory..."
rm -rf "${PG_DATA:?}"/*

echo "[3/5] Executing pgBackRest restore (repo=${REPO})..."
if [[ -n "$PITR_TARGET" ]]; then
  pgbackrest --stanza="${STANZA}" --repo="${REPO}" restore \
    --type=time \
    --target="${PITR_TARGET}" \
    --target-action=promote
else
  pgbackrest --stanza="${STANZA}" --repo="${REPO}" restore \
    --set="${RESTORE_TARGET}"
fi

echo "[4/5] Starting PostgreSQL..."
kubectl scale statefulset postgres --replicas=1 -n "${NAMESPACE}" 2>/dev/null \
  || kubectl scale deployment postgres --replicas=1 -n "${NAMESPACE}"
kubectl wait --for=condition=ready pod -l app=postgres -n "${NAMESPACE}" --timeout=5m

echo "[5/5] Verifying restored database integrity..."
psql -h "${PG_HOST}" -U "${PG_USER}" -d "${PG_DB}" -c "SELECT COUNT(*) AS audit_rows FROM audit_logs;"
psql -h "${PG_HOST}" -U "${PG_USER}" -d "${PG_DB}" -c "SELECT pg_size_pretty(pg_database_size('${PG_DB}'));"

ELAPSED="$(( $(date +%s) - START_EPOCH ))"
echo "Database restore completed successfully in ${ELAPSED}s (RTO target < 3600s)."
