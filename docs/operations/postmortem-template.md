# Postmortem: [Incident Title]

> **Blameless Principle**: This postmortem assumes every engineer acted with the best intentions using the information and tooling available at the time. Our goal is to uncover systemic weaknesses in architecture, alerting, testing, and runbooks — never to assign individual blame.

**Incident ID**: `INC-YYYYMMDD-HHMM`  
**Date of Incident**: `YYYY-MM-DD`  
**Severity**: `P0 / P1 / P2` (note any upgrades or downgrades)  
**Incident Commander**: `[Name]`  
**Postmortem Author(s)**: `[Name(s)]`  
**Reviewers**: `[Team Lead, SRE Lead, VP Engineering (for P0/P1)]`  
**Status**: `Draft | In Review | Published`  
**Incident Tracking Issue**: `[Link to GitHub / Jira / Linear issue]`  
**Confidentiality**: `Internal Only — Private Operations Repository`

---

## 1. Executive Summary

Write a concise 3–5 sentence summary explaining what happened, customer and SLO impact, root cause, and remediation.

**Worked Example (replace with incident details)**:
> On 2026-11-23 at 02:00 UTC, the PostgreSQL primary database pod (`postgresql-ha-0`) terminated due to an Out-Of-Memory (OOM) condition, triggering a **P1** incident. Patroni automatically promoted standby replica `postgresql-ha-1` within 2 minutes 30 seconds, restoring write availability with zero data loss. Users experienced an elevated `8%` HTTP `5xx` error rate and `3.2s` p99 API latency for **13 minutes** (~500 affected requests). The root cause was an undersized `2Gi` memory limit inherited from staging combined with an unindexed analytical query. Immediate remediation increased database memory limits to `8Gi`, and preventive alerting plus load-test coverage are tracked below.

---

## 2. Impact Assessment

### 2.1 User & SLO Impact
- **Start Time (UTC)**: `YYYY-MM-DD HH:MM:SS UTC`
- **Detection Time (UTC)**: `YYYY-MM-DD HH:MM:SS UTC` (MTTD: `[X min]`)
- **Acknowledgement Time (UTC)**: `YYYY-MM-DD HH:MM:SS UTC` (MTTA: `[X min]`)
- **Mitigation / Resolution Time (UTC)**: `YYYY-MM-DD HH:MM:SS UTC` (MTTR: `[X min]`)
- **Total User-Visible Duration**: `[X minutes]`
- **Affected Scope**: `[Number/percentage of requests, tenants, or agent pipelines affected]`
- **Data Loss / Corruption**: `[None | Detailed count and PITR recovery summary]`

| SLO Metric | Target | Observed Peak | Error Budget Consumed | Status |
|---|---|---|---|---|
| **SLO-01 Availability** | `>= 99.9%` (28d) | `[e.g., 97.5% during 13m window]` | `[e.g., 8.1% of 28d budget]` | `[Met / Breached]` |
| **SLO-02 API p99 Latency** | `< 1.0s` | `[e.g., 3.2s]` | `[e.g., 13 min]` | `[Met / Breached]` |
| **SLO-03 HTTP 5xx Rate** | `< 1.0%` | `[e.g., 8.0%]` | `[e.g., 13 min]` | `[Met / Breached]` |
| **SLO-04 Agent Success** | `>= 95.0%` | `[e.g., 91.2%]` | `[e.g., 13 min]` | `[Met / Breached]` |

### 2.2 Business & Operational Impact
- **Revenue / SLA Credits**: `[None | Estimated amount]`
- **Customer Support Volume**: `[Number of tickets / enterprise escalations]`
- **Compliance / Regulatory Notification Required**: `[No | Yes — reference GDPR 72h / SOC2 ticket]`
- **Reputational Impact**: `[Low / Medium / High — note time of day and external status page visibility]`

---

## 3. Incident Timeline (UTC)

Record exact timestamps in UTC. Include alert triggers, IC actions, hypotheses tested, mitigation steps, communications, and SLO recovery.

| Time (UTC) | Actor / System | Event / Action |
|---|---|---|
| `02:00:00` | Prometheus | Alert fired: `PostgreSQLPrimaryDown` (`incident_severity=P1`) |
| `02:00:15` | AlertManager | Paged Primary On-Call & IC via PagerDuty; posted to `#alerts-critical` |
| `02:03:15` | IC (`Bob`) | Acknowledged page; announced command in `#incidents`; opened tracking issue |
| `02:04:30` | IC (`Bob`) | Classified severity as `P1` (standbys healthy, partial write impact) |
| `02:05:00` | IC (`Bob`) | Assigned `Alice` to Patroni diagnostics and `Charlie` to API/PgBouncer metrics |
| `02:06:00` | `Alice` | Identified `postgresql-ha-0` terminated with `OOMKilled` (`Exit Code 137`) |
| `02:06:30` | Patroni | Detected primary lease expiration and initiated leader election |
| `02:08:00` | Patroni | Promoted `postgresql-ha-1` to new primary |
| `02:09:30` | PostgreSQL HA | Failover complete; `postgresql-ha-1` accepting read/write connections |
| `02:10:00` | PgBouncer | Application pools reconnected automatically; `5xx` error rate dropped `8% -> 3% -> 1%` |
| `02:12:00` | Grafana | API p99 latency recovered `3.2s -> 1.8s -> 0.85s`; all SLO panels green |
| `02:13:00` | IC (`Bob`) | Declared incident resolved in `#incidents` and updated stakeholders |
| `02:15:00` | IC (`Bob`) | Scheduled blameless postmortem meeting for `2026-11-24 10:00 UTC` |
| `02:16:00` | IC (`Bob`) | Created P0 action item to increase PostgreSQL memory limits (`2Gi -> 8Gi`) |

---

## 4. Root Cause Analysis (5 Whys)

Trace the causal chain from the immediate trigger to underlying systemic gaps. Include contributing factors alongside the primary cause.

1. **Why #1**: Why did the PostgreSQL primary pod crash?
   - **Answer**: The Kubernetes kubelet terminated `postgresql-ha-0` with `Exit Code 137` (`OOMKilled`).
2. **Why #2**: Why did PostgreSQL exhaust its container memory limit?
   - **Answer**: A large unindexed audit aggregation query allocated `1.8Gi` of working memory on top of shared buffers, exceeding the `2Gi` container limit.
3. **Why #3**: Why was the production memory limit set to only `2Gi`?
   - **Answer**: Initial Helm values were sized from staging datasets (`< 100MB`) and were never recalibrated after production volume grew.
4. **Why #4**: Why didn't load testing or release gates catch the memory ceiling?
   - **Answer**: Pre-release k6 load tests did not include large analytical query profiles against production-scale table sizes.
5. **Why #5**: Why didn't monitoring warn the team before the OOM kill occurred?
   - **Answer**: `PodMemoryHigh` fires after 5 minutes above `85%`, whereas a single unindexed query spiked RSS from `62%` to `100%` in under 45 seconds, and `work_mem` / slow-query alerting was not enforced.

### Systemic Root Cause Statement
> Production PostgreSQL pods operated with staging-derived `2Gi` memory limits and lacked both query-level memory caps (`work_mem` / `statement_timeout`) and production-scale analytical query load testing.

### Contributing Factors
- Patroni failover completed in `2m 30s` rather than the `< 30s` target due to TTL/loop wait configuration.
- Runbook coverage for database OOM sizing existed only under generic pod OOM procedures.

### Detection & Runbook Fidelity Review
| Dimension | Observed During Incident | Improvement Opportunity |
|---|---|---|
| **Alert Signal & Lead Time** | `PostgreSQLPrimaryDown` fired after crash; `PodMemoryHigh` 5m window missed 45s RSS spike | Add fast-rate memory growth alert and slow-query alert (`> 5s`) |
| **Observability Coverage** | Grafana SLO panels and Patroni logs surfaced cause quickly | Correlate `pg_stat_activity` peak memory per query in dashboard |
| **Runbook Accuracy** | [Database Primary Down](../runbooks/incident-database-primary-down.md) matched reality | Add explicit `work_mem` and `statement_timeout` diagnostic checks |
| **Tooling & Automation** | Automatic Patroni failover executed without human intervention | Tune failover TTL/loop wait to achieve `< 30s` consistently |

---

## 5. What Went Well vs. What Went Wrong

### What Went Well
- Automatic Patroni HA failover prevented a prolonged P0 outage and preserved **zero data loss** (`RPO = 0`).
- IC acknowledged within **3 minutes 15 seconds** (well inside the 15-minute P1 SLA) and prevented duplicate troubleshooting.
- Responders executed [Database Primary Down](../runbooks/incident-database-primary-down.md) cleanly.
- Clear status updates were posted in `#incidents` every 2–3 minutes.

### What Went Wrong
- Database container memory limits were never right-sized for production query cardinality.
- Reactive `5m` memory alerts fired too late to intercept sudden query-driven RSS spikes.
- Staging k6 scenarios lacked large result-set database workloads.
- Patroni leader election took `2m 30s` instead of `< 30s`, extending partial write errors.

---

## 6. Follow-Up Action Items

Every action item MUST be linked to a tracked issue (`Jira` / `Linear` / `GitHub`) tagged `postmortem-action` and reviewed weekly until closed.

### Prevent Recurrence (P0 — Due <= 48 hours)
- [ ] **Action 1**: Increase PostgreSQL StatefulSet memory limits (`2Gi -> 8Gi`) and cap `work_mem` / `statement_timeout`
  - **Owner**: `[Database Lead]`
  - **Due Date**: `[YYYY-MM-DD]`
  - **Tracker Ticket**: `[JIRA-123 / LIN-123 / #123]`
  - **Verification**: Deployed via Helm; 48h Grafana memory peak remains `< 60%` of limit
- [ ] **Action 2**: Add missing composite index on high-cardinality audit/run query columns
  - **Owner**: `[Backend Engineer]`
  - **Due Date**: `[YYYY-MM-DD]`
  - **Tracker Ticket**: `[JIRA-123 / LIN-123 / #123]`
  - **Verification**: `EXPLAIN ANALYZE` confirms index scan `< 25ms`

### Improve Detection (P1 — Due <= 2 weeks)
- [ ] **Action 3**: Add Prometheus trend alert for sustained container memory growth (`> 10%/day`) and slow query count (`> 5s`)
  - **Owner**: `[SRE Engineer]`
  - **Due Date**: `[YYYY-MM-DD]`
  - **Tracker Ticket**: `[JIRA-123 / LIN-123 / #123]`
  - **Verification**: Alert rule unit-tested in `tests/monitoring/` and verified in staging
- [ ] **Action 4**: Tune Patroni `ttl`, `loop_wait`, and `retry_timeout` to reduce automatic failover duration `< 30s`
  - **Owner**: `[Database / SRE Engineer]`
  - **Due Date**: `[YYYY-MM-DD]`
  - **Tracker Ticket**: `[JIRA-123 / LIN-123 / #123]`
  - **Verification**: Staging `tests/resilience/test_failover.sh` passes `< 30s`

### Improve Response & Testing (P2 — Due <= 30 days)
- [ ] **Action 5**: Add production-scale analytical query workload to nightly k6 stress & soak suite
  - **Owner**: `[QA / Performance Engineer]`
  - **Due Date**: `[YYYY-MM-DD]`
  - **Tracker Ticket**: `[JIRA-123 / LIN-123 / #123]`
  - **Verification**: `k6-soak-test.js` runs clean without OOM or RSS drift
- [ ] **Action 6**: Update [Pod OOM Kill Runbook](../runbooks/incident-oom-kill.md) with database `work_mem` and `pg_stat_activity` inspection steps
  - **Owner**: `[SRE Engineer]`
  - **Due Date**: `[YYYY-MM-DD]`
  - **Tracker Ticket**: `[JIRA-123 / LIN-123 / #123]`
  - **Verification**: PR merged and `pytest tests/operations/ -v` passes

### Organizational Learning (P3 — Due <= 1 quarter)
- [ ] **Action 7**: Document quarterly resource capacity review checklist using `scripts/capacity-planning.py`
  - **Owner**: `[Tech Lead]`
  - **Due Date**: `[YYYY-MM-DD]`
  - **Tracker Ticket**: `[JIRA-123 / LIN-123 / #123]`
  - **Verification**: Completed in Q1 capacity planning review

---

## 7. Lessons Learned

1. **Staging data volume does not predict production memory spikes**: Always benchmark with production-like cardinality and query plans.
2. **Initial resource limits are hypotheses, not constants**: Revisit CPU/memory requests and limits continuously as usage scales.
3. **HA investments pay immediate dividends**: Patroni streaming replication turned a potential multi-hour P0 outage into a 13-minute zero-data-loss P1.
4. **Proactive query guards complement infrastructure alerts**: `statement_timeout` and `work_mem` bounds protect PostgreSQL faster than 5-minute metric evaluation windows.
5. **Runbooks plus a single IC compress MTTR**: Clear role assignment eliminated duplicate investigation and kept communication steady.

---

## 8. Supporting Evidence & References

- **Incident Tracking Issue**: `[Link]`
- **Grafana SLO Window**: `[Link to pvs-slo-dashboard with ?from=...&to=...]`
- **Grafana Incident Metrics**: [incident-metrics.json](../../grafana/dashboards/incident-metrics.json)
- **Jaeger Error Traces**: `[Link to Jaeger query for incident window]`
- **Executed Runbook(s)**: [Database Primary Down](../runbooks/incident-database-primary-down.md) · [Pod OOM Kill](../runbooks/incident-oom-kill.md)
- **Redacted Log Archive**: `[Link to private S3/log snapshot]`

---

## 9. Security Redaction & Sign-Off

### Security & Privacy Verification
- [ ] Zero API keys, database credentials, or bearer tokens present in postmortem or attachments
- [ ] Zero customer PII, emails, or raw prompt payloads present in timeline or log snippets
- [ ] Stored in private operations repository with appropriate access controls

### Approval & Distribution
- [ ] Reviewed by: `[Engineering Team Lead]` — Date: `[YYYY-MM-DD]`
- [ ] Reviewed by: `[SRE Lead]` — Date: `[YYYY-MM-DD]`
- [ ] Reviewed by: `[VP Engineering]` *(required for P0/P1)* — Date: `[YYYY-MM-DD]`
- [ ] Retrospective meeting held: `[YYYY-MM-DD HH:MM UTC]` — Attendees: `[Names]`
- [ ] Action items created and linked in Jira / Linear / GitHub
- [ ] Key lessons shared with Engineering at all-hands / `#engineering`
