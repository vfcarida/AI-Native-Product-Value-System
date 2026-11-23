# Incident Severity Classification Guide

**Owner**: SRE Team  
**Last Updated**: 2026-11-23

---

## Overview

Every incident is assigned a severity level (**P0–P4**) based on **business and user impact**, not technical complexity. Classification must happen rapidly (within minutes of acknowledgement) and must never delay active mitigation.

### Quick Comparison Matrix

| Level | Label | User Impact | Acknowledge SLA | Target MTTR | Incident Commander | Status Page | Escalation | Postmortem |
|---|---|---|---|---|---|---|---|---|
| **P0** | Critical — Service Down | All users impacted / complete outage | `<= 5 min` | `< 15 min` | Dual IC (Senior + Lead) | `<= 10 min`, q15m | `15 min` -> VP Eng | Required (`<= 48h`) |
| **P1** | Major — Partial Outage | Many users / critical feature degraded | `<= 15 min` | `< 1 hour` | Single On-Call IC | `<= 30 min` if ongoing | `1 hour` -> Team Lead | Required (`<= 1 week`) |
| **P2** | Minor — Localized Issue | Some users / redundant capacity degraded | `<= 30 min` | `< 4 hours` | Optional (On-Call) | Internal only | `4 hours` -> Team Lead | Optional |
| **P3** | Warning — Potential Issue | Zero current user impact (early warning) | `<= 2 hours` (biz) | `< 1 biz day` | None (ticket) | None | `2 biz days` -> Lead | Not required |
| **P4** | Info — Monitoring Alert | None (informational operational event) | Best effort | N/A | None | None | N/A | Not required |

---

## P0: Critical — Service Down

**Definition**: Complete service outage affecting all users, irreversible data loss risk, or active security breach.

**Examples**:
- API returns `503` for all requests (`100%` error rate)
- PostgreSQL primary down with automatic Patroni failover broken
- Confirmed security breach with active data exfiltration or audit log tampering
- Complete loss of critical control-plane functionality (authentication, policy enforcement, or all LLM execution)

**Response SLA**:
- **Acknowledge**: Immediate (`<= 5 minutes` of alert)
- **Resolution Target**: `< 15 minutes`
- **Communication**: Status page update within `10 minutes` (then every `15 minutes`), immediate executive/stakeholder notification

**Incident Commander**: Senior on-call IC + Engineering Team Lead (**dual IC**)

**Escalation**: After `15 minutes` if unresolved, escalate to VP Engineering (and Security Lead / DPO for security incidents)

**Postmortem**: Mandatory within `48 hours`

**Mapped Prometheus Alerts (`incident_severity: P0`)**:
- `SLOAvailabilityCritical` (availability error rate `> 10x` error budget)
- `SLOAvailabilityMultiWindowBurn` (`14.4x` multi-window fast burn rate)

---

## P1: Major — Partial Outage

**Definition**: Significant service degradation affecting many users or core automated agent workflows, with partial service still reachable.

**Examples**:
- API latency `p99 > 5s` sustained (users experiencing client timeouts)
- `>= 5–10%` HTTP `5xx` error rate across API traffic
- One availability zone unavailable or database failover causing transient errors
- Core agent pipeline broken (`AgentSuccessRateCritical` `< 85%`) or verified backups failing (`BackupFailed`)

**Response SLA**:
- **Acknowledge**: Within `15 minutes`
- **Resolution Target**: `< 1 hour`
- **Communication**: Status page update within `30 minutes` if ongoing `> 30 min`; updates every `30 minutes`

**Incident Commander**: On-call Incident Commander

**Escalation**: After `1 hour` if unresolved, escalate to Team Lead / Engineering Manager

**Postmortem**: Mandatory within `1 week` (5 business days)

**Mapped Prometheus Alerts (`incident_severity: P1`)**:
- `SLOLatencyP99Critical` (`p99 > 5s` for 2m)
- `SLOErrorRateCritical` (`5xx > 5%` for 2m)
- `AgentSuccessRateCritical` (`completion rate < 85%` for 5m)
- `BackupFailed` (backup or weekly restore verification failure)

---

## P2: Minor — Localized Issue

**Definition**: Degraded performance, intermittent errors, or reduced redundancy affecting a subset of traffic while overall availability remains intact.

**Examples**:
- API latency `p99 > 1s` (`<= 5s`) — slow but functional
- `1%–5%` HTTP `5xx` error rate (intermittent failures)
- Single pod unhealthy or memory/CPU saturated (`> 85%` memory / `> 25%` CPU throttling) while peer replicas absorb traffic
- Non-critical workflow or cache layer degraded with L1 fallback active

**Response SLA**:
- **Acknowledge**: Within `30 minutes`
- **Resolution Target**: `< 4 hours` (business hours)
- **Communication**: Internal `#incidents` / `#alerts-warnings` updates only (no status page update unless upgraded)

**Incident Commander**: On-call engineer (optional; may self-resolve following linked runbook)

**Escalation**: After `4 hours` if unresolved, escalate to Team Lead

**Postmortem**: Optional (recommended if novel failure mode or repeat occurrence)

**Mapped Prometheus Alerts (`incident_severity: P2`)**:
- `SLOAvailabilityBudgetBurn`
- `SLOLatencyP99High`, `SLOLatencyP95Sustained`
- `SLOErrorRateHigh`
- `AgentSuccessRateLow`
- `PodMemoryHigh`, `PodCPUThrottled`

---

## P3: Warning — Potential Issue

**Definition**: Early warning signal indicating emerging risk with no current end-user impact.

**Examples**:
- Last backup older than 2 hours (`BackupOld`) or anomalously small (`BackupSizeTooSmall`)
- PersistentVolumeClaim disk utilization `> 80%` (trending toward full)
- Slow database query trend detected before impacting API SLOs
- TLS certificate or Vault credential approaching expiration (`< 30 days`)

**Response SLA**:
- **Acknowledge**: Within `2 hours` (business hours only)
- **Resolution Target**: `< 1 business day`
- **Communication**: Internal ticket created; no urgent paging

**Incident Commander**: Not assigned (standard engineering ticket workflow)

**Escalation**: After `2 business days` if unresolved, escalate to Team Lead

**Postmortem**: Not required

**Mapped Prometheus Alerts (`incident_severity: P3`)**:
- `BackupOld`
- `BackupSizeTooSmall`

---

## P4: Info — Monitoring Alert

**Definition**: Informational operational signal requiring no immediate action; useful for trend visibility or audit correlation.

**Examples**:
- Production Helm deployment completed cleanly
- HPA auto-scaling event during expected business-hour traffic ramp
- Non-error log volume increase or expected cache hit rate shift after prompt release

**Response SLA**:
- **Acknowledge**: Best effort (business hours)
- **Resolution Target**: N/A (investigate during routine operational review)
- **Communication**: None (routed to `email-info` / AlertManager history)

**Incident Commander**: Not assigned

**Postmortem**: Not required

---

## Prometheus Alert Severity Mapping

AlertManager routes notifications using the standard `severity` label (`critical`, `warning`, `info`), while every rule in [prometheusrule.yaml](../../deploy/helm/product-value-system/templates/prometheusrule.yaml) ALSO carries an explicit `incident_severity` label (`P0`–`P4`) for automatic incident classification:

| Prometheus `severity` | AlertManager Channel | `incident_severity` Range |
|---|---|---|
| `critical` | PagerDuty + Slack `#alerts-critical` | `P0`, `P1` |
| `warning` | Slack `#alerts-warnings` | `P2`, `P3` |
| `info` | Email (`email-info`) | `P4` |

---

## Severity Upgrade & Downgrade Rules

Incidents dynamically change severity as user impact evolves:
- **Upgrade** (e.g., `P2 -> P1` or `P1 -> P0`): Triggered immediately if error rate climbs, blast radius expands, or mitigation stalls past SLA.
- **Downgrade** (e.g., `P0 -> P1` or `P1 -> P2`): Triggered once emergency mitigation (failover, rollback, rate limit) restores core availability while cleanup continues.
- **IC Authority**: The Incident Commander may upgrade or downgrade severity unilaterally. Always record the timestamp and business-impact rationale in the incident timeline and update communication cadence accordingly.

---

## Related Documentation

- [Incident Management Process](incident-management.md)
- [Incident Commander (IC) Guide](incident-commander-guide.md)
- [Communication Templates](communication-templates.md)
- [Alert Runbook Index](alert-runbook-index.md)
- [Production SLOs & Error Budgets](slos.md)
