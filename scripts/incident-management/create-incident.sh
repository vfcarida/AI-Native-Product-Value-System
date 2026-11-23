#!/usr/bin/env bash
# Create a structured GitHub incident tracking issue using the PROD-013 incident workflow.
# Usage:
#   create-incident.sh "Title" "P1" "Summary" [--ic handle] [--alert AlertName] [--dry-run]
#   create-incident.sh --title "Title" --severity P1 --summary "Summary" [--dry-run]
set -euo pipefail

TITLE=""
SEVERITY=""
SUMMARY=""
INCIDENT_COMMANDER="${INCIDENT_COMMANDER:-unassigned}"
ALERT_NAME="${ALERT_NAME:-manual-declaration}"
RUNBOOK_PATH="${RUNBOOK_PATH:-docs/runbooks/oncall-quick-reference.md}"
DRY_RUN="${INCIDENT_DRY_RUN:-false}"
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --title) TITLE="$2"; shift 2 ;;
    --severity) SEVERITY="$2"; shift 2 ;;
    --summary) SUMMARY="$2"; shift 2 ;;
    --ic) INCIDENT_COMMANDER="$2"; shift 2 ;;
    --alert) ALERT_NAME="$2"; shift 2 ;;
    --runbook) RUNBOOK_PATH="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help)
      echo "Usage: $0 <title> <P0|P1|P2|P3|P4> <summary> [--ic <user>] [--alert <name>] [--runbook <path>] [--dry-run]"
      exit 0
      ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      exit 2
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [[ -z "${TITLE}" && ${#POSITIONAL[@]} -ge 1 ]]; then
  TITLE="${POSITIONAL[0]}"
fi
if [[ -z "${SEVERITY}" && ${#POSITIONAL[@]} -ge 2 ]]; then
  SEVERITY="${POSITIONAL[1]}"
fi
if [[ -z "${SUMMARY}" && ${#POSITIONAL[@]} -ge 3 ]]; then
  SUMMARY="${POSITIONAL[2]}"
fi

if [[ -z "${TITLE}" || -z "${SEVERITY}" ]]; then
  echo "ERROR: Both title and severity (P0-P4) are required." >&2
  exit 2
fi

SEVERITY="${SEVERITY^^}"
case "${SEVERITY}" in
  P0) ACK_SLA="5 min"; MTTR_TARGET="< 15 min"; POSTMORTEM_REQ="Mandatory (<= 48h)"; STATUS_PAGE="Required (<= 10 min)" ;;
  P1) ACK_SLA="15 min"; MTTR_TARGET="< 1 hour"; POSTMORTEM_REQ="Mandatory (<= 1 week)"; STATUS_PAGE="Required if ongoing (<= 30 min)" ;;
  P2) ACK_SLA="30 min"; MTTR_TARGET="< 4 hours"; POSTMORTEM_REQ="Optional"; STATUS_PAGE="Internal only" ;;
  P3) ACK_SLA="2 hours (business)"; MTTR_TARGET="< 1 business day"; POSTMORTEM_REQ="Not required"; STATUS_PAGE="None" ;;
  P4) ACK_SLA="Best effort"; MTTR_TARGET="N/A"; POSTMORTEM_REQ="Not required"; STATUS_PAGE="None" ;;
  *)
    echo "ERROR: Invalid severity '${SEVERITY}'. Must be one of: P0, P1, P2, P3, P4." >&2
    exit 2
    ;;
esac

NOW_UTC="$(date -u +"%Y-%m-%d %H:%M UTC")"
INCIDENT_ID="INC-$(date -u +"%Y%m%d-%H%M")"
ISSUE_TITLE="[INCIDENT][${SEVERITY}] ${INCIDENT_ID}: ${TITLE}"
LABELS="incident,severity:${SEVERITY}"

ISSUE_BODY="$(cat <<EOF
## Incident Metadata

- **Incident ID**: \`${INCIDENT_ID}\`
- **Severity**: \`${SEVERITY}\` (Ack SLA: ${ACK_SLA} | Target MTTR: ${MTTR_TARGET})
- **Status**: \`Investigating\`
- **Incident Commander (IC)**: \`${INCIDENT_COMMANDER}\`
- **Triggering Alert**: \`${ALERT_NAME}\`
- **Runbook**: \`${RUNBOOK_PATH}\`
- **Start Time (UTC)**: \`${NOW_UTC}\`
- **Status Page Policy**: ${STATUS_PAGE}
- **Postmortem Requirement**: ${POSTMORTEM_REQ}

---

## Executive Summary

${SUMMARY:-Initial incident declaration; diagnosis in progress.}

---

## UTC Timeline

| Time (UTC) | Actor / System | Event / Action |
|---|---|---|
| \`${NOW_UTC}\` | Automation / IC | Incident \`${INCIDENT_ID}\` opened with severity \`${SEVERITY}\` |

---

## Follow-Up Checklist

- [ ] SLOs verified green for >= 10 minutes
- [ ] Secrets and PII verified redacted
- [ ] Postmortem scheduled/completed (if P0/P1)
EOF
)"

echo "=== Create Incident (${INCIDENT_ID} | ${SEVERITY}) ==="
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Would create GitHub issue:"
  echo "  Title:  ${ISSUE_TITLE}"
  echo "  Labels: ${LABELS}"
  echo "  SLA:    Ack ${ACK_SLA} | Target MTTR ${MTTR_TARGET} | Postmortem: ${POSTMORTEM_REQ}"
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: GitHub CLI ('gh') is required for live issue creation. Re-run with --dry-run to preview." >&2
  exit 1
fi

gh issue create --title "${ISSUE_TITLE}" --label "${LABELS}" --body "${ISSUE_BODY}"
echo "Created incident issue for ${INCIDENT_ID}."
