# Weekly Incident & Monthly Team Retrospective Template

**Owner**: SRE & Engineering Team  
**Cadence**: Weekly Incident Retro (Mondays, 10:00 AM, 30–45 min) · Monthly Team Retro (First Monday, 14:00, 60 min)  
**Last Updated**: 2026-11-23

---

## Blameless Ground Rules

1. Focus exclusively on **systems, alerts, architecture, and runbooks** — never individuals.
2. Assume every responder made the best possible decision with the signals available at the time.
3. Convert every recurring friction point into a single-owner ticket on the [SRE Kaizen Board](../../.github/projects/kaizen-board.yml).

---

## Part A: Weekly Incident Retrospective (30–45 min)

**Week Of**: `YYYY-MM-DD`  
**Facilitator**: `[Name]`  
**Outgoing On-Call / IC**: `[Name]`  
**Attendees**: `[Names]`

### 1. Incident Summary Table (Past 7 Days — P0 to P2)

| Incident ID | Severity | Category | Duration / MTTR | Runbook Used | Postmortem Status | Kaizen Ticket |
|---|---|---|---|---|---|---|
| `INC-YYYYMMDD-HHMM` | `P1` | `database` | `13 min` | `incident-database-primary-down.md` | Published | `KAIZEN-101` |

### 2. Alert Quality & Noise Audit
- **Total Pages Sent (PagerDuty)**: `[Count]` *(Target: `< 5 pages/week`)*
- **False Positives / Silenced Alerts**: `[Count / %]` *(Target: `< 5%`)*
- **Noisiest Alerts to Tune**:
  - `[AlertName]` — Proposed threshold/window adjustment: `[Detail]`
- **Missing Signals (Blind Spots)**:
  - `[Symptom detected by user before Prometheus alerted]`

### 3. Pattern & Recurrence Check
- Are any two incidents from the last 30 days sharing a root-cause category? `[Yes / No]`
- Did any runbook contain outdated commands or missing decision branches? `[List PRs to update docs/runbooks/]`
- Are all P0/P1 postmortem action items on track for their SLA due dates? `[Yes / List overdue items]`

### 4. Weekly Actions Promoted to Kaizen Board
- [ ] `[P0/P1/P2]` `[Action description]` — **Owner**: `@handle` — **Due**: `YYYY-MM-DD` — **Label**: `reliability` / `observability` / `automation`

---

## Part B: Monthly Engineering & SRE Retrospective (60 min)

**Month**: `YYYY-MM`  
**Facilitator**: `[Name]`

### 1. Previous Action Items Check (10 min)
- Completed: `[Count / %]`
- Carried over & blockers: `[Items]`

### 2. What Went Well This Month (10 min)
- `[Reliability win, GameDay insight, automated toil reduction, DORA gain]`

### 3. What Could Be Improved (10 min)
- `[Bottleneck in CI/CD, slow staging feedback, noisy alerts, manual toil]`

### 4. Toil & Team Health Snapshot (10 min)
- **Measured Monthly Toil**: `[XX%]` *(Target: `< 30%`)*
- **Top Toil Source**: `[Description + automation ROI estimate]`
- **GameDay Outcome**: `[Scenario ID — Green / Yellow / Red]`
- **DORA Highlights**: `[Deployment freq, Lead time, CFR, MTTR delta]`

### 5. Prioritized Next-Month Commitments (15 min)
Limit to **3–5 concrete Kaizen items** so work finishes cleanly:
1. `[Item 1]` — Owner: `@handle` — Target Metric: `[Metric]`
2. `[Item 2]` — Owner: `@handle` — Target Metric: `[Metric]`
3. `[Item 3]` — Owner: `@handle` — Target Metric: `[Metric]`

### 6. Recognition & Shout-Outs (5 min)
- Celebrate calm IC leadership, runbook improvements, and proactive reliability fixes.

---

## Related Documentation

- [SRE Team Rituals](team-rituals.md)
- [Blameless Postmortem Template](postmortem-template.md)
- [Improvement Metrics (DORA & SPACE)](improvement-metrics.md)
- [SRE Kaizen Board](../../.github/projects/kaizen-board.yml)
