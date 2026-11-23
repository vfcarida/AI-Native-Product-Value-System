# On-Call Handbook & Incident Response Guide

This handbook defines on-call responsibilities, alert routing channels, escalation policies, incident triage procedures, and maintenance window silencing for the AI-Native Product Value System.

---

## Responsibilities

As the active on-call engineer, you are responsible for:
- Acknowledging **critical** PagerDuty pages within **5 minutes** (MTTA target `< 15 min`).
- Investigating and mitigating customer-impacting production incidents using linked runbooks.
- Escalating to the backup on-call engineer if unable to stabilize within **15–30 minutes**.
- Communicating status updates in `#incidents` and completing blameless postmortems for P0/P1 events.
- Performing structured handoffs at weekly shift rotation boundaries.

---

## On-Call Rotation & Escalation Policy

Defined declaratively in [config/pagerduty-schedule.yaml](../../config/pagerduty-schedule.yaml):
- **Primary On-Call**: 3+ engineers on a weekly rotation (`604,800s`, Mon 09:00 ET -> Mon 09:00 ET).
- **Backup On-Call**: 2+ senior engineers on a weekly rotation.
- **Escalation Chain** (`num_loops: 2`):
  1. `0 min` -> Primary On-Call Schedule
  2. `15 min` -> Backup On-Call Schedule
  3. `30 min` -> Engineering Manager (`engineering-manager@example.com`)

---

## Alert Routing Channels

Configured in [deploy/helm/dependencies/alertmanager-config.yaml](../../deploy/helm/dependencies/alertmanager-config.yaml):

| Severity | Primary Delivery | Secondary Delivery | Group Wait / Repeat |
|---|---|---|---|
| **Critical** | PagerDuty (`pagerduty-critical`, `< 1 min` page) | Slack `#alerts-critical` (`continue: true`) | `10s` wait / `4h` repeat |
| **Warning** | Slack `#alerts-warnings` (no 24/7 page) | AlertManager UI | `10s` wait / `4h` repeat |
| **Info** | Email (`engineering-notifications@example.com`) | AlertManager UI | `10s` wait / `4h` repeat |

**Inhibition Rules** (preventing alert storms):
- `ServiceDown` suppresses downstream alerts for the same `(service, cluster)`.
- `NodeDown` suppresses `PodCrashLooping`, `PodMemoryHigh`, and `PodCPUThrottled` on the same `node`.

---

## 5-Step Incident Response Procedure

1. **Acknowledge (`< 5 min`)**: Click **Acknowledge** in PagerDuty to stop escalation and post `"Investigating <INCIDENT_ID>"` in `#incidents`.
2. **Assess Severity**:
   - **P0 (Critical / Customer Impact)**: Immediate mitigation, freeze deployments.
   - **P1 (Degraded SLO)**: Investigate within 15 min; MTTR target `< 1 hour`.
   - **P2 (Warning)**: Triage during business hours; track in issue tracker.
3. **Execute Linked Runbook**: Open the `runbook_url` attached to the alert (or look up the alert in [Alert Runbook Index](alert-runbook-index.md)).
4. **Escalate When Needed**:
   - Unresolved after 30 min -> Page backup / Engineering Manager.
   - Suspected credential compromise -> Execute [Emergency Secret Revocation Runbook](runbooks/emergency-secret-revocation.md).
   - Suspected audit tamper / GDPR breach -> Follow 72-hour breach procedure in [GDPR Compliance](../compliance/gdpr-compliance.md).
5. **Resolve & Document**: Confirm SLO metrics recover on the Grafana SLO Dashboard (`pvs-slo-dashboard`), mark the incident **Resolved** in PagerDuty, and file a blameless postmortem within 48 hours for P0/P1 incidents.

---

## Maintenance Window Silencing

Use [scripts/silence-alert.sh](../../scripts/silence-alert.sh) to create audited AlertManager silences before planned maintenance:

```bash
# Preview silence payload without mutating AlertManager
./scripts/silence-alert.sh --duration 2h --comment "PostgreSQL schema migration" --dry-run

# Create 2-hour maintenance silence (optionally notifies Slack if SLACK_WEBHOOK_URL is set)
./scripts/silence-alert.sh --duration 2h --comment "PostgreSQL schema migration"
```

---

## Related Documentation

- [Alert Runbook Index](alert-runbook-index.md)
- [Production SLOs & Error Budgets](slos.md)
- [Distributed Tracing with Jaeger](tracing-guide.md)
- [High-Latency Trace Runbook](../runbooks/trace-high-latency.md)
