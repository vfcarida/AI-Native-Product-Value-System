#!/usr/bin/env bash
# Diagnose slow PostgreSQL queries (>THRESHOLD_MS) and sequential-scan index gaps (PROD-012).
set -euo pipefail

NAMESPACE="${NAMESPACE:-product-value-system}"
POSTGRES_POD="${POSTGRES_POD:-product-value-system-postgres-ha-0}"
PG_USER="${PG_USER:-pvs_user}"
PG_DB="${PG_DB:-pvs_db}"
THRESHOLD_MS="${THRESHOLD_MS:-100}"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --pod) POSTGRES_POD="$2"; shift 2 ;;
    --threshold-ms) THRESHOLD_MS="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

echo "=== Slow Query Diagnosis (${NAMESPACE}/${POSTGRES_POD}, >${THRESHOLD_MS}ms) ==="
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Would query pg_stat_statements (mean_exec_time > ${THRESHOLD_MS}ms) and pg_stat_user_tables."
  exit 0
fi

ENABLED="$(kubectl exec -n "${NAMESPACE}" "${POSTGRES_POD}" -- \
  psql -U "${PG_USER}" -d "${PG_DB}" -tAc "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements';")"
if [[ "${ENABLED}" != "1" ]]; then
  echo "ERROR: pg_stat_statements extension is not enabled." >&2
  exit 1
fi

echo "--- Top Slow Queries (>${THRESHOLD_MS}ms) ---"
kubectl exec -n "${NAMESPACE}" "${POSTGRES_POD}" -- \
  psql -U "${PG_USER}" -d "${PG_DB}" -c "
  SELECT substring(query, 1, 100) AS query_snippet,
         calls,
         round(mean_exec_time::numeric, 2) AS avg_ms,
         round(total_exec_time::numeric, 2) AS total_ms
  FROM pg_stat_statements
  WHERE mean_exec_time > ${THRESHOLD_MS}
  ORDER BY mean_exec_time DESC
  LIMIT 10;
  "

echo "--- High Sequential Scan Tables ---"
kubectl exec -n "${NAMESPACE}" "${POSTGRES_POD}" -- \
  psql -U "${PG_USER}" -d "${PG_DB}" -c "
  SELECT schemaname, tablename, seq_scan, idx_scan
  FROM pg_stat_user_tables
  WHERE seq_scan > 100
  ORDER BY seq_scan DESC
  LIMIT 10;
  "
