# Incident Commander (IC) Guide

**Owner**: SRE & Engineering Leadership  
**Last Updated**: 2026-11-23

---

## Role Purpose

The **Incident Commander (IC)** is the single point of coordination during a production incident. The IC does **not** debug or execute fixes solo — the IC orchestrates responders, protects focus, maintains the authoritative timeline, drives timely stakeholder communication, and ensures clean handoff to postmortem learning.

---

## Core Responsibilities

### 1. Take Command
- Acknowledge PagerDuty page within SLA (`<= 5 min` for P0, `<= 15 min` for P1).
- Announce command in `#incidents`: `"IC: [Your Name] — Investigating [AlertName / Symptom]"`.
- Open the incident tracking issue using [create-incident.sh](../../scripts/incident-management/create-incident.sh) or [.github/ISSUE_TEMPLATE/incident-report.md](../../.github/ISSUE_TEMPLATE/incident-report.md).
- Start capturing UTC timestamps for all major decisions and state transitions.

### 2. Assess & Maintain Severity
- Classify the incident (`P0`–`P4`) using [Incident Severity Guide](incident-severity-guide.md) based on business impact.
- Upgrade immediately if blast radius widens; downgrade once mitigation limits user impact.
- Document every severity change and rationale in the incident timeline.

### 3. Coordinate Response (Delegate, Don't Duplicate)
- Assign clear roles so responders never collide:
  - **Operations / Diagnostics Lead**: Executes the linked runbook (`docs/runbooks/incident-*.md`), inspects Jaeger traces, logs, and PostgreSQL/Kubernetes state.
  - **Communications Lead** (required for P0, optional for P1): Drafts and posts status page updates and stakeholder summaries using [Communication Templates](communication-templates.md).
  - **Scribe** (recommended for P0): Captures verbatim UTC timeline entries, commands run, and dashboard links.
- Prevent uncoordinated actions (`"Hold pod restarts until Diagnostics confirms DB pool state"`).
- Keep responders anchored to runbooks before attempting untested interventions.
- Escalate proactively to Database, Security, or Leadership specialists whenever recovery approaches SLA limits.

### 4. Maintain Authoritative UTC Timeline
Record every key milestone with UTC timestamps in the incident issue:
```text
00:00 - Alert fired: SLOLatencyP99High (incident_severity=P2 -> upgraded P1)
00:03 - IC (Alice) acknowledged in #incidents
00:05 - Diagnosis: PgBouncer connection pool saturated (20/20 active)
00:07 - Mitigation: Increased PgBouncer pool size 20 -> 40 via Helm upgrade
00:12 - Verification: API p99 latency dropped from 5.4s to 0.78s
00:15 - Incident declared resolved; postmortem scheduled
```

### 5. Communicate Status on Cadence
- **P0**: Status page update within `10 min`, then every `15 min` until resolved; immediate executive notification.
- **P1**: Status page update within `30 min` if ongoing; updates every `30 min`.
- **P2**: Internal `#incidents` updates every `60 min` (no external status page unless upgraded).
- Use [update-status-page.sh](../../scripts/incident-management/update-status-page.sh) and [notify-stakeholders.sh](../../scripts/incident-management/notify-stakeholders.sh) to keep formatting consistent.

### 6. Declare Resolution & Initiate Learning
- Verify all SLO metrics (`Availability`, `p99 Latency`, `5xx Error Rate`, `Agent Success`) are green on Grafana for `>= 10 minutes` and Prometheus alerts have cleared.
- Post final resolution notice in `#incidents`, status page, and stakeholder channels.
- Schedule the blameless postmortem (`<= 48h` for P0, `<= 1 week` for P1) and assign a postmortem author.
- Hand off follow-up action items to the designated engineering owner and thank all responders.

---

## IC Rotation & Qualification

Configured in [config/pagerduty-schedule.yaml](../../config/pagerduty-schedule.yaml) (`Product Value System - Incident Commander (IC)`):
- **Eligibility**: Senior engineers and SREs with `> 6 months` system tenure.
- **Cadence**: 1-week primary IC rotation (Mon 09:00 ET -> Mon 09:00 ET), aligned with or independent of primary on-call.
- **Onboarding Prerequisite**: Shadow at least **2** live or GameDay incidents and complete the 1-hour incident management workshop before taking solo IC shifts.
- **Backup & Handoff**: Engineering Team Lead serves as backup IC when the scheduled IC is unavailable or during P0 dual-IC response. For long incidents (`> 2 hours`), perform an explicit spoken/written IC handoff (`"Handoff: Bob -> Carol as IC at 04:00 UTC"`).

---

## Decision Authority

| Action | IC Authority | Notes |
|---|---|---|
| Upgrade or downgrade incident severity (`P0`–`P4`) | **Authorized (unilateral)** | Record rationale in timeline |
| Assign diagnostic, mitigation, and comms roles | **Authorized** | Prevent duplicate effort |
| Trigger Helm rollback or scale-up automation | **Authorized** | Follow [Rollback Procedure](../runbooks/rollback-procedure.md) |
| Escalate to Database, Security, or VP Engineering | **Authorized** | Mandatory when SLA at risk |
| Declare incident resolved & schedule postmortem | **Authorized** | Requires green SLO verification |
| Approve permanent architecture or schema redesigns | **Not authorized** | Defer to post-incident ADR/PR review |
| Send unapproved external PR/legal statements | **Not authorized** | Coordinate with Product / Legal / Security |

---

## IC Operational Checklist

### During Incident
- [ ] Acknowledge PagerDuty page within SLA and announce `"IC: [Name]"` in `#incidents`
- [ ] Create incident issue (`scripts/incident-management/create-incident.sh`)
- [ ] Classify severity (`P0`–`P4`) and announce blast radius
- [ ] Assign Diagnostics, Scribe, and Comms roles (do not solo-debug)
- [ ] Link and follow the matching runbook (`docs/runbooks/incident-*.md`)
- [ ] Maintain UTC timeline in incident issue
- [ ] Publish status page & stakeholder updates on cadence (P0/P1)
- [ ] Verify all SLOs green (`>= 10 min`) and alerts cleared
- [ ] Post `"Incident Resolved"` message across all active channels

### After Incident
- [ ] Schedule blameless postmortem meeting (`P0: <= 48h`, `P1: <= 1 week`)
- [ ] Assign postmortem author using [postmortem-template.md](postmortem-template.md)
- [ ] Ensure secrets/PII are redacted from incident issue and logs
- [ ] Confirm follow-up action tickets created in Jira/Linear/GitHub
- [ ] Thank all responders and complete shift handoff notes

---

## Worked Example: P1 IC Timeline

**Incident**: `INC-20261123-0200` — PostgreSQL Primary Failover (`P1`)

```text
2026-11-23 02:00:00 UTC - Alert fired: PostgreSQLPrimaryDown (incident_severity=P1)
2026-11-23 02:03:15 UTC - IC (Bob) acknowledged: "IC: Bob — Investigating PostgreSQL primary down"
2026-11-23 02:04:30 UTC - Severity confirmed P1 (standbys healthy, partial write interruption)
2026-11-23 02:05:00 UTC - Roles assigned: Alice (Patroni logs), Charlie (API 5xx & PgBouncer metrics)
2026-11-23 02:07:00 UTC - Diagnosis: Primary pod postgresql-ha-0 terminated (OOMKilled, Exit 137)
2026-11-23 02:08:00 UTC - Action: Patroni initiated automatic leader election to postgresql-ha-1
2026-11-23 02:09:30 UTC - Verification: Failover complete; postgresql-ha-1 accepting writes
2026-11-23 02:10:00 UTC - Application pods reconnected via PgBouncer; 5xx rate dropping
2026-11-23 02:12:00 UTC - SLOs green (5xx < 1%, API p99 < 1s)
2026-11-23 02:13:00 UTC - IC declared incident resolved in #incidents
2026-11-23 02:15:00 UTC - Postmortem scheduled: 2026-11-24 10:00 UTC (Author: Alice)
2026-11-23 02:16:00 UTC - P0 follow-up ticket assigned: Increase DB memory limit 2Gi -> 8Gi (Dave)
```

**Measured MTTR**: `13 minutes` (vs P1 target `< 1 hour`)

---

## Related Documentation

- [Incident Management Process](incident-management.md)
- [Incident Severity Guide (P0–P4)](incident-severity-guide.md)
- [Communication Templates](communication-templates.md)
- [Blameless Postmortem Template](postmortem-template.md)
- [On-Call Handbook](oncall-guide.md)
