#!/usr/bin/env bash
# Check whether a production deployment freeze / blackout window is active (PROD-014).
# Exits 0 if clear to deploy, 1 if a deployment freeze is active (unless emergency override approved).
set -euo pipefail

CHECK_DATE="${CHECK_DATE:-$(date -u +"%Y-%m-%d")}"
CHECK_DAY_OF_WEEK="${CHECK_DAY_OF_WEEK:-}"
EMERGENCY_OVERRIDE="${DEPLOYMENT_FREEZE_OVERRIDE:-false}"
OVERRIDE_REASON="${FREEZE_OVERRIDE_REASON:-}"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date) CHECK_DATE="$2"; shift 2 ;;
    --emergency-override) EMERGENCY_OVERRIDE="true"; shift ;;
    --reason) OVERRIDE_REASON="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help)
      echo "Usage: $0 [--date YYYY-MM-DD] [--emergency-override --reason <text>] [--dry-run]"
      exit 0
      ;;
    *) echo "ERROR: Unknown option: $1" >&2; exit 2 ;;
  esac
done

resolve_python() {
  local candidate
  for candidate in "${PYTHON_BIN:-}" python3 python; do
    if [[ -n "${candidate}" ]] && command -v "${candidate}" >/dev/null 2>&1; then
      if "${candidate}" -c "import sys; raise SystemExit(0)" >/dev/null 2>&1; then
        echo "${candidate}"
        return 0
      fi
    fi
  done
  echo "ERROR: No working python3/python interpreter found." >&2
  return 1
}

if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Would evaluate deployment freeze calendar for UTC date ${CHECK_DATE} (override=${EMERGENCY_OVERRIDE})."
  exit 0
fi

PYTHON_BIN="$(resolve_python)"

"${PYTHON_BIN}" - "${CHECK_DATE}" "${EMERGENCY_OVERRIDE}" "${OVERRIDE_REASON}" <<'PYEOF'
import datetime
import sys

check_date_str, override_str, reason = sys.argv[1:4]
override = override_str.strip().lower() in {"1", "true", "yes"}

try:
    target = datetime.date.fromisoformat(check_date_str)
except ValueError as exc:
    raise SystemExit(f"ERROR: Invalid date '{check_date_str}' (expected YYYY-MM-DD): {exc}")

# Annual recurring & scheduled production blackout windows (synced with docs/operations/deployment-freeze-calendar.md)
# Format: (start_month, start_day, end_month, end_day, label)
RECURRING_WINDOWS = [
    (11, 25, 11, 30, "US Thanksgiving / Black Friday Freeze"),
    (12, 20, 12, 31, "Year-End Holiday Freeze"),
    (1, 1, 1, 3, "New Year Holiday Freeze"),
]

active_reason = None
for sm, sd, em, ed, label in RECURRING_WINDOWS:
    start = datetime.date(target.year, sm, sd)
    end = datetime.date(target.year, em, ed)
    if start <= target <= end:
        active_reason = f"{label} ({start.isoformat()} to {end.isoformat()})"
        break

if active_reason is None and target.weekday() in (5, 6):
    active_reason = f"Weekend Production Freeze ({target.strftime('%A')})"

if active_reason:
    if override:
        if not reason.strip():
            print("ERROR: Emergency freeze override requires a non-empty --reason / FREEZE_OVERRIDE_REASON.", file=sys.stderr)
            sys.exit(2)
        print(f"WARNING: Freeze active ({active_reason}), bypassed via audited P0 emergency override: {reason}")
        sys.exit(0)
    print(f"BLOCKED: Production deployment freeze active on {target.isoformat()} — {active_reason}", file=sys.stderr)
    print("See docs/operations/deployment-freeze-calendar.md for emergency P0 hotfix override procedure.", file=sys.stderr)
    sys.exit(1)

print(f"OK: No production deployment freeze active on {target.isoformat()}.")
sys.exit(0)
PYEOF
