# SRE Team Rituals & Operational Culture

**Owner**: SRE & Engineering Leadership  
**Last Updated**: 2026-11-23

---

## 1. Daily Rituals

### On-Call Handoff (Every Morning, 09:00 Local / ET)
- **Duration**: 15 minutes
- **Participants**: Outgoing on-call engineer, incoming on-call engineer, Team Lead (optional)
- **Format**:
  1. Outgoing on-call reports incidents handled (severity, MTTR), toil encountered, noisy/false-positive alerts, and pending runbook updates.
  2. Incoming on-call confirms system health (all Grafana SLOs green), planned deployments/maintenance windows, and active escalation contacts.
  3. Post concise handoff summary in `#oncall`.

---

## 2. Weekly Rituals

### Incident Retrospective (Every Monday, 10:00 AM)
- **Duration**: 30–45 minutes · **Participants**: All engineers
- **Template**: [Weekly Retrospective Template](retrospective-template.md)
- **Format**: Review past week's P0–P2 incidents (5 min each), audit alert noise and runbook fidelity, identify repeating patterns, and promote prioritized fixes to the [SRE Kaizen Board](../../.github/projects/kaizen-board.yml).

### DORA Metrics Review (Every Friday, 15:00)
- **Duration**: 15 minutes · **Participants**: All engineers
- **Dashboard**: [DORA & Improvement Metrics](../../grafana/dashboards/improvement-metrics.json)
- **Format**: Review Deployment Frequency, Lead Time, Change Failure Rate, and MTTR trends; celebrate reliability gains; surface top pipeline or operational blockers; share snapshot in `#metrics`.

---

## 3. Monthly Rituals

### Chaos Engineering GameDay (Last Friday, 14:00–16:00 UTC)
- **Duration**: 2 hours · **Participants**: All engineers (rotate Facilitator and Chaos Injector roles)
- **Guides**: [GameDay Facilitation Guide](gameday-facilitation-guide.md) · [GameDay Scenarios](gameday-scenarios/README.md)
- **Output**: GitHub `gameday` issue documenting measured SLOs, outcome (`Green`/`Yellow`/`Red`), and Kaizen follow-up tickets.

### Team Retrospective (First Monday, 14:00–15:00)
- **Duration**: 60 minutes · **Participants**: All engineers + Team Lead
- **Format**: Review prior action items (10m), what went well (10m), what to improve (10m), commit to 3–5 Kaizen items (15m), celebrate wins (5m), open Q&A (10m).

### Toil Measurement & Automation Triage (Last Monday, 13:00–14:00)
- **Duration**: 60 minutes · **Participants**: All engineers
- **Format**: Tally manual repetitive operational work (`(Toil hours / Total hours) * 100`, target **`< 30%`**), rank highest-ROI automation opportunities, and schedule automation stories on the Kaizen board.

---

## 4. Quarterly Rituals

### Disaster Recovery (DR) Drill (Second Wednesday, 10:00–12:00 UTC)
- **Duration**: 2 hours · **Participants**: All engineers + Engineering Manager
- **Procedure**: [Quarterly DR Drill Procedures](dr-drill-procedures.md)
- **Output**: Timed RTO (`< 1h`) and RPO (`< 15m`) compliance scorecard + Kaizen tickets.

### Quarterly OKR Planning (First Week of Quarter)
- **Duration**: 2 hours · **Participants**: All engineers + Team Lead + Manager
- **Format**: Review prior OKRs, analyze 90-day DORA/SPACE/SLO trends, and commit to measurable reliability, automation, and skill-building Key Results (e.g., reduce P1 incidents by 50%, raise GameDay pass rate `>= 90%`, automate 3 manual runbooks).

### Quarterly Team Training & Shadowing (2 Hours / Month)
- **Month 1**: Deep-dive technical workshop (Patroni HA, Jaeger tracing, Vault, Cedar policy engine).
- **Month 2**: Chaos Engineering facilitation & Chaos Mesh safety training.
- **Month 3**: Incident Commander role-play & blameless 5-Whys facilitation practice.

---

## 5. Annual Rituals

### Year-in-Review Reliability Summit (December, Full Day)
- **Participants**: Engineering, SRE, Leadership, Product Stakeholders
- **Agenda**: Review 12-month DORA/SLO/incident/deployment trends, celebrate major resilience wins, prioritize multi-quarter architectural investments, and publish the annual operational roadmap.

---

## 6. Blameless Culture Principles

- **Focus on Systems, Not Individuals**: Ask *"How did our tooling, alerts, or guardrails permit this failure?"* — never *"Who broke it?"*.
- **Reward Early Transparency**: Surfacing near-misses and mistakes quickly is celebrated as an act of engineering leadership.
- **Learning Over Punishment**: Every outage or failed GameDay strengthens the platform when its lessons ship as code, tests, or alerts.

---

## 7. Psychological Safety

- **No Gatekeeping Questions**: Junior and senior engineers alike are explicitly encouraged to question runbooks, alert thresholds, and architectural assumptions.
- **Safe Experimentation**: Staging GameDays and dry-run flags give engineers safe sandboxes to practice high-stakes recoveries before a 3 AM page.

---

## 8. Learning Mindset & Knowledge Sharing

- **20% Engineering Investment**: Dedicate `>= 20%` capacity to Kaizen improvements, toil automation, GameDays, and technical training.
- **Shadowing Before Solo Shifts**: Every engineer shadows `>= 2` incidents before taking solo IC or primary on-call rotations.
- **Write It Down**: Every undocumented discovery goes directly into `docs/runbooks/` or `docs/operations/`.

---

## 9. Work-Life Balance & Sustainable On-Call

- **Healthy Rotation Depth**: Maintain `>= 3` primary on-call engineers (1 week on, `>= 2–3` weeks off) per [config/pagerduty-schedule.yaml](../../config/pagerduty-schedule.yaml).
- **Anti-Hero Culture**: Rely on HA automation, PDBs, auto-rollback, and team handoffs — never individual heroics.
- **Off-Hours Respect**: Avoid non-urgent Slack messages outside business hours; route non-critical alerts (`P3`/`P4`) exclusively to business-hours queues.

---

## 10. Quarterly Team Health Metrics

Track quarterly via anonymous survey (`aggregate_only: true`) and operational telemetry:

| Team Health Signal | Target | Action Trigger |
|---|---|---|
| **Operational Toil Percentage** | `< 30%` | `>= 35%` -> Freeze non-critical features; run automation sprint |
| **On-Call Paging Burden** | `< 5 pages / week` | `>= 8 pages / week` -> Mandatory alert tuning & noisy-rule audit |
| **Deployment Frequency** | `>= Weekly` (target Daily) | Drop `> 30%` -> Audit CI/CD bottlenecks |
| **Change Failure Rate** | `< 15%` | `>= 20%` -> Strengthen staging smoke tests & pre-deploy checks |
| **P1 MTTR** | `< 1 hour` | `> 60 min` -> Review runbooks & GameDay scenarios |
| **Team Satisfaction (SPACE)** | `> 4.0 / 5.0` | `< 3.8` -> Leadership listening session & workload rebalance |
| **Dedicated Improvement / Learning Time** | `>= 20%` | `< 15%` -> Protect sprint Kaizen capacity |

---

## Related Documentation

- [Improvement Metrics (DORA & SPACE)](improvement-metrics.md)
- [GameDay Facilitation Guide](gameday-facilitation-guide.md)
- [Weekly Retrospective Template](retrospective-template.md)
- [Quarterly DR Drill Procedures](dr-drill-procedures.md)
- [On-Call Handbook](oncall-guide.md)
- [SRE Kaizen Board](../../.github/projects/kaizen-board.yml)
