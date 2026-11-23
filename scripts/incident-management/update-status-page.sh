#!/usr/bin/env bash
# Publish structured incident updates to Statuspage.io / Atlassian Statuspage (PROD-013).
# Reads STATUSPAGE_PAGE_ID and STATUSPAGE_API_KEY from environment variables (never hardcoded).
set -euo pipefail

INCIDENT_ID=""
SEVERITY="P1"
STATUS="investigating"
TITLE=""
MESSAGE=""
DRY_RUN="${INCIDENT_DRY_RUN:-false}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --incident-id) INCIDENT_ID="$2"; shift 2 ;;
    --severity) SEVERITY="$2"; shift 2 ;;
    --status) STATUS="$2"; shift 2 ;;
    --title) TITLE="$2"; shift 2 ;;
    --message) MESSAGE="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help)
      echo "Usage: $0 --incident-id <INC-ID> --severity <P0|P1|P2> --status <investigating|identified|monitoring|resolved> --title <text> --message <text> [--dry-run]"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

SEVERITY="${SEVERITY^^}"
STATUS="${STATUS,,}"

case "${STATUS}" in
  investigating|identified|monitoring|resolved) ;;
  *)
    echo "ERROR: Invalid status '${STATUS}'. Must be: investigating, identified, monitoring, or resolved." >&2
    exit 2
    ;;
esac

case "${SEVERITY}" in
  P0) IMPACT_OVERRIDE="critical" ;;
  P1) IMPACT_OVERRIDE="major" ;;
  P2) IMPACT_OVERRIDE="minor" ;;
  P3|P4) IMPACT_OVERRIDE="none" ;;
  *)
    echo "ERROR: Invalid severity '${SEVERITY}'. Must be one of: P0, P1, P2, P3, P4." >&2
    exit 2
    ;;
esac

if [[ -z "${TITLE}" || -z "${MESSAGE}" ]]; then
  echo "ERROR: Both --title and --message are required." >&2
  exit 2
fi

if [[ "${SEVERITY}" == "P3" || "${SEVERITY}" == "P4" ]]; then
  echo "INFO: Severity ${SEVERITY} does not publish external status page updates per policy."
  exit 0
fi

INCIDENT_LABEL="${INCIDENT_ID:+[${INCIDENT_ID}] }${TITLE}"
PAYLOAD="$(python3 -c '
import json, sys
print(json.dumps({
    "incident": {
        "name": sys.argv[1],
        "status": sys.argv[2],
        "impact_override": sys.argv[3],
        "body": sys.argv[4],
    }
}))
' "${INCIDENT_LABEL}" "${STATUS}" "${IMPACT_OVERRIDE}" "${MESSAGE}" 2>/dev/null || python -c '
import json, sys
print(json.dumps({
    "incident": {
        "name": sys.argv[1],
        "status": sys.argv[2],
        "impact_override": sys.argv[3],
        "body": sys.argv[4],
    }
}))
' "${INCIDENT_LABEL}" "${STATUS}" "${IMPACT_OVERRIDE}" "${MESSAGE}")"

echo "=== Status Page Update (${SEVERITY} -> ${IMPACT_OVERRIDE} | ${STATUS}) ==="
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Would POST status update payload:"
  echo "${PAYLOAD}"
  exit 0
fi

if [[ -z "${STATUSPAGE_PAGE_ID:-}" || -z "${STATUSPAGE_API_KEY:-}" ]]; then
  echo "ERROR: STATUSPAGE_PAGE_ID and STATUSPAGE_API_KEY must be set in environment." >&2
  exit 1
fi

curl -fsS -X POST \
  "https://api.statuspage.io/v1/pages/${STATUSPAGE_PAGE_ID}/incidents" \
  -H "Authorization: OAuth ${STATUSPAGE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}" >/dev/null

echo "Status page updated cleanly (${STATUS})."
