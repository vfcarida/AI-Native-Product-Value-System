#!/usr/bin/env bash
# Dispatch severity-tiered stakeholder notifications via Slack/webhook (PROD-013).
# Reads SLACK_WEBHOOK_URL and optional STAKEHOLDER_WEBHOOK_URL from environment variables.
set -euo pipefail

INCIDENT_ID="INC-$(date -u +"%Y%m%d-%H%M")"
SEVERITY="P1"
STATUS="investigating"
INCIDENT_COMMANDER="unassigned"
TITLE=""
SUMMARY=""
ISSUE_URL=""
DRY_RUN="${INCIDENT_DRY_RUN:-false}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --incident-id) INCIDENT_ID="$2"; shift 2 ;;
    --severity) SEVERITY="$2"; shift 2 ;;
    --status) STATUS="$2"; shift 2 ;;
    --ic) INCIDENT_COMMANDER="$2"; shift 2 ;;
    --title) TITLE="$2"; shift 2 ;;
    --summary) SUMMARY="$2"; shift 2 ;;
    --issue-url) ISSUE_URL="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help)
      echo "Usage: $0 --severity <P0|P1|P2|P3|P4> --title <text> --summary <text> [--incident-id <id>] [--status <state>] [--ic <name>] [--issue-url <url>] [--dry-run]"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

SEVERITY="${SEVERITY^^}"
case "${SEVERITY}" in
  P0) AUDIENCE="#incidents, Executive Leadership (VP Eng), All Stakeholders"; CADENCE="every 15 min" ;;
  P1) AUDIENCE="#incidents, Engineering Leadership"; CADENCE="every 30 min" ;;
  P2) AUDIENCE="#incidents (internal engineering only)"; CADENCE="every 60 min" ;;
  P3) AUDIENCE="Internal ticket queue (business hours)"; CADENCE="daily" ;;
  P4) AUDIENCE="Informational log only"; CADENCE="none" ;;
  *)
    echo "ERROR: Invalid severity '${SEVERITY}'. Must be one of: P0, P1, P2, P3, P4." >&2
    exit 2
    ;;
esac

if [[ -z "${TITLE}" ]]; then
  echo "ERROR: --title is required." >&2
  exit 2
fi

MSG="[${SEVERITY}][${STATUS^^}] ${INCIDENT_ID}: ${TITLE} | IC: ${INCIDENT_COMMANDER} | Audience: ${AUDIENCE} | Next update: ${CADENCE} | Summary: ${SUMMARY:-Investigating}${ISSUE_URL:+ | Issue: ${ISSUE_URL}}"
PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"text": sys.argv[1]}))' "${MSG}" 2>/dev/null || python -c 'import json, sys; print(json.dumps({"text": sys.argv[1]}))' "${MSG}")"

echo "=== Stakeholder Notification (${INCIDENT_ID} | ${SEVERITY}) ==="
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Audience: ${AUDIENCE}"
  echo "[DRY-RUN] Payload:  ${PAYLOAD}"
  exit 0
fi

SENT="false"
if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
  curl -fsS -X POST "${SLACK_WEBHOOK_URL}" -H "Content-Type: application/json" -d "${PAYLOAD}" >/dev/null
  SENT="true"
fi

if [[ "${SEVERITY}" == "P0" || "${SEVERITY}" == "P1" ]] && [[ -n "${STAKEHOLDER_WEBHOOK_URL:-}" ]]; then
  curl -fsS -X POST "${STAKEHOLDER_WEBHOOK_URL}" -H "Content-Type: application/json" -d "${PAYLOAD}" >/dev/null
  SENT="true"
fi

if [[ "${SENT}" != "true" ]]; then
  echo "ERROR: Neither SLACK_WEBHOOK_URL nor STAKEHOLDER_WEBHOOK_URL is configured. Use --dry-run to preview." >&2
  exit 1
fi

echo "Stakeholder notifications dispatched (${AUDIENCE})."
