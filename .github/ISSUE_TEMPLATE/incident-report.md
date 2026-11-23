---
name: Incident Report
about: Formal tracking issue for production or staging incidents (P0–P4)
title: "[INCIDENT][P?] "
labels: ["incident"]
assignees: ""
---

## Incident Metadata

- **Incident ID**: `INC-YYYYMMDD-HHMM`
- **Severity**: `P0` / `P1` / `P2` / `P3` / `P4` *(see `docs/operations/incident-severity-guide.md`)*
- **Status**: `Investigating` / `Identified` / `Monitoring` / `Resolved`
- **Incident Commander (IC)**: `@handle`
- **Responders**: `@diagnostics-lead`, `@comms-lead`, `@scribe`
- **Triggering Alert(s)**: `[AlertName]`
- **Executed Runbook**: `[docs/runbooks/incident-*.md]`
- **Start Time (UTC)**: `YYYY-MM-DD HH:MM UTC`
- **Resolution Time (UTC)**: `YYYY-MM-DD HH:MM UTC` *(MTTR: X min)*

---

## Executive Summary

> One-paragraph plain-language summary of user impact, blast radius, and current status. Do **not** include raw credentials, tokens, or customer PII.

---

## Impact & SLO Assessment

- **Affected Service / Component**: `[API / PostgreSQL HA / Agent Pipeline / Sandbox / Vault]`
- **User / Request Impact**: `[Error rate %, latency p99, affected tenants]`
- **Data Loss / Security Impact**: `[None | Describe containment]`
- **SLOs Affected**:
  - [ ] SLO-01 Availability (`99.9%`)
  - [ ] SLO-02 API Latency (`p99 < 1s`)
  - [ ] SLO-03 HTTP 5xx Error Rate (`< 1%`)
  - [ ] SLO-04 Agent Success Rate (`>= 95%`)

---

## UTC Timeline

| Time (UTC) | Actor / System | Event / Action |
|---|---|---|
| `HH:MM` | AlertManager | Alert fired |
| `HH:MM` | IC | Acknowledged & declared severity |
| `HH:MM` | Diagnostics | Root cause hypothesis / finding |
| `HH:MM` | Operations | Mitigation applied |
| `HH:MM` | IC | SLOs verified green; incident resolved |

---

## Communication Log

- [ ] `#incidents` initial announcement posted
- [ ] Status page updated (`P0 <= 10 min`, `P1 <= 30 min`)
- [ ] Stakeholders / leadership notified (P0/P1)
- [ ] Resolution notice posted across all active channels

---

## Root Cause & Mitigation Summary

- **Immediate Mitigation**: `[Rollback / Scale-out / Failover / Config fix]`
- **Confirmed Root Cause**: `[Brief summary — expand in postmortem for P0/P1]`

---

## Postmortem & Follow-Up Action Items

- **Postmortem Required**: `[Yes (P0 <= 48h, P1 <= 1 week) | Optional (P2) | No (P3/P4)]`
- **Postmortem Link**: `[Link to completed postmortem from docs/operations/postmortem-template.md]`
- **Follow-Up Tickets** (`postmortem-action`):
  - [ ] `[P0]` Action item 1 — Owner: `@handle` — Due: `YYYY-MM-DD`
  - [ ] `[P1]` Action item 2 — Owner: `@handle` — Due: `YYYY-MM-DD`

---

## Closure Verification

- [ ] All SLO dashboards green for `>= 10 minutes` and alerts resolved
- [ ] Secrets, credentials, and customer PII verified redacted
- [ ] Linked runbook updated with any newly discovered diagnosis or mitigation steps
