# Incident Communication Templates

**Owner**: SRE & Engineering Leadership  
**Last Updated**: 2026-11-23

---

## Communication Principles & Privacy Guardrails

1. **Timely & Predictable**: Commit to the next update time in every message (`P0: every 15 min`, `P1: every 30 min`, `P2: every 60 min`).
2. **Impact First, Jargon Second**: Lead with what users experience, whether mitigation is underway, and when the next update will arrive.
3. **Never Speculate on Root Cause**: State verified observations only until confirmed.
4. **Zero Secrets or PII**: Never paste API keys, database DSNs, internal private IPs, stack traces containing customer data, or raw LLM prompts into Slack, status pages, or emails.

---

## 1. Slack Templates (`#incidents`)

### 1.1 Initial Incident Declaration (IC)
```markdown
:rotating_light: **INCIDENT DECLARED — [INC-YYYYMMDD-HHMM] | [P0 / P1 / P2]**
- **Title**: [Short impact summary, e.g., Elevated API 5xx Error Rate]
- **Incident Commander**: @[IC-Name]
- **Diagnostics Lead**: @[Responder-Name]
- **Comms Lead**: @[Comms-Name or IC]
- **Impact**: [Who/what is affected, e.g., ~12% of API requests returning 503]
- **Tracking Issue**: [Link to GitHub/Jira issue]
- **Runbook**: [Link to docs/runbooks/incident-*.md]
- **Next Update**: [HH:MM UTC] (in [15/30] min)
```

### 1.2 Periodic Progress Update
```markdown
:mag: **UPDATE #[N] — [INC-YYYYMMDD-HHMM] | [P0 / P1 / P2] | [Investigating / Identified / Monitoring]**
- **Current Status**: [What changed since last update]
- **Mitigation Underway**: [Specific action, e.g., Rolling back Helm release to rev 41]
- **Current Metrics**: [e.g., Error rate 8% -> 2.1%, p99 latency 2.4s]
- **Next Update**: [HH:MM UTC]
```

### 1.3 Severity Change or Escalation Notice
```markdown
:warning: **ESCALATION / SEVERITY CHANGE — [INC-YYYYMMDD-HHMM]**
- **Change**: `[P2 -> P1]` (or Escalated to `[Database Lead / Security On-Call / VP Engineering]`)
- **Reason**: [Business impact rationale, e.g., Error rate crossed 10% across both zones]
- **IC**: @[IC-Name] | **Tracking Issue**: [Link]
- **Next Update**: [HH:MM UTC]
```

### 1.4 Incident Resolved Notice
```markdown
:white_check_mark: **INCIDENT RESOLVED — [INC-YYYYMMDD-HHMM] | [P0 / P1 / P2]**
- **Resolution Time**: [YYYY-MM-DD HH:MM UTC] (Total Duration / MTTR: `[X min]`)
- **Mitigation Applied**: [What restored service]
- **Verification**: All SLOs green on Grafana (`pvs-slo-dashboard`) for >= 10 minutes
- **Postmortem**: [Required — Scheduled for YYYY-MM-DD HH:MM UTC, Author: @Name | Not required for P2]
- **Thank you** to @[Responders] for rapid mitigation!
```

---

## 2. Status Page Templates (P0 & P1 External/Internal Visibility)

Automate delivery via [scripts/incident-management/update-status-page.sh](../../scripts/incident-management/update-status-page.sh).

### 2.1 Investigating (`<= 10 min` for P0, `<= 30 min` for P1)
> **Title**: `[Degraded Performance / Service Disruption] — AI-Native Product Value System API`  
> **Status**: `Investigating`  
> **Message**:  
> We are currently investigating reports of `[elevated latency / failed requests]` affecting `[API / agent execution]` workflows. Our engineering team is actively diagnosing the issue and working to restore normal service. We will provide our next update within `[15 / 30]` minutes.

### 2.2 Identified
> **Status**: `Identified`  
> **Message**:  
> We have identified the underlying cause of the `[elevated latency / service disruption]` affecting `[affected component]` and are actively applying a mitigation. We will share another update within `[15 / 30]` minutes as recovery progresses.

### 2.3 Monitoring
> **Status**: `Monitoring`  
> **Message**:  
> A mitigation has been deployed and key service health metrics have returned to normal thresholds. We are closely monitoring stability and will confirm full resolution once sustained health is verified.

### 2.4 Resolved
> **Status**: `Resolved`  
> **Message**:  
> This incident has been resolved as of `[HH:MM UTC]`. All API and agent services are operating normally. We apologize for the disruption and are conducting a thorough post-incident review to prevent recurrence.

---

## 3. Stakeholder Email Templates (Leadership & Enterprise Accounts)

Automate dispatch via [scripts/incident-management/notify-stakeholders.sh](../../scripts/incident-management/notify-stakeholders.sh).

### 3.1 P0 / P1 Active Incident Notification
```text
Subject: [INCIDENT][P0/P1] [INC-YYYYMMDD-HHMM] — [Short Title] (Investigating)

Summary:
At [HH:MM UTC], we detected [brief description of business impact] affecting [scope].

Current Status:
- Severity: [P0 — Critical / P1 — Major]
- Incident Commander: [Name]
- Customer Impact: [Plain-language description; no internal hostnames or secrets]
- Current Action: [Active mitigation underway]
- Status Page: [URL]

Next Update:
We will send the next update by [HH:MM UTC] (or sooner if resolved).
```

### 3.2 Final Resolution & Postmortem Summary
```text
Subject: [RESOLVED][P0/P1] [INC-YYYYMMDD-HHMM] — [Short Title] (MTTR: [X min])

Summary:
The [P0/P1] incident that began at [Start HH:MM UTC] was fully resolved at [End HH:MM UTC] (total duration: [X minutes]).

Impact & Mitigation:
- Impact: [Concise summary of affected requests/services; confirm whether any data loss occurred]
- Mitigation: [Summary of fix applied]
- Verification: All SLO metrics (availability, p99 latency, error rate, agent success) have returned to target levels.

Follow-Up:
A blameless postmortem will be completed and shared by [YYYY-MM-DD] with preventive action items tracked in our engineering backlog.
```

---

## 4. Issue Tracker Action Item Format (Jira / Linear / GitHub)

Use this standardized payload when creating follow-up tickets from postmortems:

```markdown
**Labels**: `postmortem-action`, `incident:INC-YYYYMMDD-HHMM`, `priority:P0`
**Summary**: `[INC-YYYYMMDD-HHMM] [Concrete preventive or detection action]`
**Description**:
- **Originating Postmortem**: [Link to private postmortem]
- **Problem Addressed**: [Which 5-Whys factor this eliminates]
- **Acceptance & Verification**: [Objective test, alert check, or load-test proof]
- **Due Date**: [YYYY-MM-DD per SLA]
```

---

## Related Documentation

- [Incident Management Process](incident-management.md)
- [Incident Severity Guide (P0–P4)](incident-severity-guide.md)
- [Incident Commander (IC) Guide](incident-commander-guide.md)
- [Blameless Postmortem Template](postmortem-template.md)
