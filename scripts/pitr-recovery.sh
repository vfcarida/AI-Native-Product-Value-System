#!/usr/bin/env bash
# Point-in-Time Recovery (PITR) wrapper validating WAL archive coverage before restore (PROD-010).
# Usage:
#   ./scripts/pitr-recovery.sh "2026-11-23 10:30:00" [--dry-run] [--yes]

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 \"YYYY-MM-DD HH:MM:SS\" [--dry-run] [--yes]" >&2
  exit 2
fi

TARGET_TIME="$1"
shift
EXTRA_ARGS=("$@")
S3_BUCKET="${BACKUP_S3_BUCKET:-pvs-backups}"
S3_REGION="${BACKUP_S3_REGION:-us-west-2}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Point-in-Time Recovery to: ${TARGET_TIME}"

# Validate timestamp format portably using Python
TARGET_EPOCH="$(TARGET_TIME="$TARGET_TIME" python3 - <<'PY'
import os
from datetime import UTC, datetime

raw = os.environ["TARGET_TIME"].strip()
for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S%z"):
    try:
        dt = datetime.strptime(raw, fmt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        print(int(dt.timestamp()))
        raise SystemExit(0)
    except ValueError:
        continue
raise SystemExit(f"Invalid timestamp format: {raw!r} (expected YYYY-MM-DD HH:MM:SS)")
PY
)"

if [[ " ${EXTRA_ARGS[*]:-} " == *" --dry-run "* ]]; then
  echo "[DRY-RUN] Validated PITR target epoch=${TARGET_EPOCH}; skipping live S3 WAL lookup."
  exec "${SCRIPT_DIR}/restore-database.sh" --pitr "${TARGET_TIME}" "${EXTRA_ARGS[@]}"
fi

EARLIEST_WAL="$(aws s3 ls "s3://${S3_BUCKET}/archive/" --region "${S3_REGION}" --recursive | head -n 1 | awk '{print $1" "$2}')"
if [[ -z "${EARLIEST_WAL}" ]]; then
  echo "No WAL archives found in s3://${S3_BUCKET}/archive/" >&2
  exit 1
fi

echo "Earliest WAL archive: ${EARLIEST_WAL}"
EARLIEST_EPOCH="$(date -u -d "${EARLIEST_WAL}" +%s)"
if [[ "${TARGET_EPOCH}" -lt "${EARLIEST_EPOCH}" ]]; then
  echo "Target time (${TARGET_TIME}) precedes earliest WAL archive (${EARLIEST_WAL})." >&2
  exit 1
fi

echo "Target time is within recoverable WAL range."
exec "${SCRIPT_DIR}/restore-database.sh" --pitr "${TARGET_TIME}" "${EXTRA_ARGS[@]}"
