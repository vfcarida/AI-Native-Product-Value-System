#!/usr/bin/env bash
# Silence Prometheus AlertManager alerts during scheduled maintenance windows (PROD-008).
# Usage:
#   ./scripts/silence-alert.sh --duration 2h --comment "Database migration"
#   ./scripts/silence-alert.sh --duration 30m --matcher "service=product-value-system" --dry-run

set -euo pipefail

ALERTMANAGER_URL="${ALERTMANAGER_URL:-http://alertmanager.monitoring.svc.cluster.local:9093}"
DURATION="${DURATION:-2h}"
COMMENT="${COMMENT:-Scheduled maintenance}"
CREATOR="${CREATOR:-$(whoami 2>/dev/null || echo 'oncall-engineer')}"
MATCHER_NAME="${MATCHER_NAME:-alertname}"
MATCHER_VALUE="${MATCHER_VALUE:-.*}"
MATCHER_REGEX="${MATCHER_REGEX:-true}"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --duration) DURATION="$2"; shift 2 ;;
    --comment) COMMENT="$2"; shift 2 ;;
    --creator) CREATOR="$2"; shift 2 ;;
    --alertmanager-url) ALERTMANAGER_URL="$2"; shift 2 ;;
    --matcher-name) MATCHER_NAME="$2"; shift 2 ;;
    --matcher-value) MATCHER_VALUE="$2"; shift 2 ;;
    --exact) MATCHER_REGEX="false"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

# Compute UTC startsAt / endsAt and safely JSON-encode payload via Python (avoids shell injection)
PAYLOAD="$(
  DURATION="$DURATION" \
  COMMENT="$COMMENT" \
  CREATOR="$CREATOR" \
  MATCHER_NAME="$MATCHER_NAME" \
  MATCHER_VALUE="$MATCHER_VALUE" \
  MATCHER_REGEX="$MATCHER_REGEX" \
  python3 - <<'PY'
import json, os, re
from datetime import UTC, datetime, timedelta

raw_dur = os.environ["DURATION"].strip()
m = re.fullmatch(r"(\d+)([mhd])", raw_dur)
if not m:
    raise SystemExit(f"Invalid --duration '{raw_dur}' (expected e.g. 30m, 2h, 1d)")
qty, unit = int(m.group(1)), m.group(2)
delta = {"m": timedelta(minutes=qty), "h": timedelta(hours=qty), "d": timedelta(days=qty)}[unit]

now = datetime.now(UTC)
payload = {
    "matchers": [
        {
            "name": os.environ["MATCHER_NAME"],
            "value": os.environ["MATCHER_VALUE"],
            "isRegex": os.environ["MATCHER_REGEX"].lower() == "true",
            "isEqual": True,
        }
    ],
    "startsAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "endsAt": (now + delta).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "createdBy": os.environ["CREATOR"],
    "comment": os.environ["COMMENT"],
}
print(json.dumps(payload))
PY
)"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] POST ${ALERTMANAGER_URL}/api/v2/silences"
  echo "$PAYLOAD"
  exit 0
fi

RESPONSE="$(curl -fsS -X POST "${ALERTMANAGER_URL}/api/v2/silences" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")"

SILENCE_ID="$(echo "$RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("silenceID",""))')"

echo "Silence created: ${SILENCE_ID}"
echo "  Duration: ${DURATION}"
echo "  Comment:  ${COMMENT}"
echo "  Creator:  ${CREATOR}"

# Optional Slack notification (requires SLACK_WEBHOOK_URL env var — never hardcoded)
if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
  SLACK_MSG="$(SILENCE_ID="$SILENCE_ID" DURATION="$DURATION" COMMENT="$COMMENT" CREATOR="$CREATOR" \
    python3 -c 'import json,os; print(json.dumps({"text": f":mute: Alerts silenced for {os.environ[\"DURATION\"]} (Silence ID: {os.environ[\"SILENCE_ID\"]})\n*Reason*: {os.environ[\"COMMENT\"]}\n*By*: {os.environ[\"CREATOR\"]}"}))')"
  curl -fsS -X POST "${SLACK_WEBHOOK_URL}" \
    -H "Content-Type: application/json" \
    -d "$SLACK_MSG" >/dev/null
fi
