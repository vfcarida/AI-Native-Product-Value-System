# Production Deployment Freeze Calendar

**Owner**: Release Engineering & SRE Team  
**Last Updated**: 2026-11-23  
**Enforcement Script**: [scripts/deployment/check-freeze.sh](../../scripts/deployment/check-freeze.sh)

---

## 1. Purpose

Production deployment freezes reduce outage risk during peak customer traffic, low-staffing holidays, and weekend periods when on-call staffing is lean. Automated enforcement in [.github/workflows/deploy-hardened.yml](../../.github/workflows/deploy-hardened.yml) blocks non-emergency production releases during active blackout windows while preserving full staging deployments for continuous testing.

---

## 2. Scheduled Blackout Windows (2025–2027)

### Annual Recurring Holiday Freezes (Enforced by `check-freeze.sh`)

| Freeze Window | Dates (UTC) | Scope | Staging Allowed? | Production Allowed? |
|---|---|---|---|---|
| **New Year Holiday Freeze** | `Jan 01 – Jan 03` (2025, 2026, 2027) | All production services | Yes | **Blocked** (P0 hotfix override only) |
| **US Thanksgiving & Black Friday Freeze** | `Nov 25 – Nov 30` (2025, 2026, 2027) | All production services | Yes | **Blocked** (P0 hotfix override only) |
| **Year-End Holiday Freeze** | `Dec 20 – Dec 31` (2025, 2026, 2027) | All production services | Yes | **Blocked** (P0 hotfix override only) |

### Weekly & Quarterly Freeze Policies

| Policy Window | Schedule (UTC) | Enforcement Level | Notes |
|---|---|---|---|
| **Weekend Freeze** | `Saturday 00:00 – Sunday 23:59 UTC` | **Hard Block** (`check-freeze.sh`) | Prevents low-staffing weekend rollouts |
| **Friday Afternoon Cutoff** | `Friday 18:00 – 23:59 UTC` | Soft Freeze (Release Lead sign-off) | Prefer Monday–Thursday morning windows |
| **End-of-Quarter Close** | Final 2 business days of Q1/Q2/Q3/Q4 | Change Freeze (VP Eng approval) | Protects enterprise billing & audit close |
| **Active SLO Error Budget Freeze** | Whenever 28d SLO error budget remaining `<= 25%` | **Hard Block** per [SLO Policy](slos.md) | Reliability & bug fixes only until recovered |

---

## 3. Automated Verification & Local Testing

Run [scripts/deployment/check-freeze.sh](../../scripts/deployment/check-freeze.sh) before cutting a release:

```bash
# Check today's UTC date
bash scripts/deployment/check-freeze.sh

# Test a specific planned release date
bash scripts/deployment/check-freeze.sh --date 2026-11-10

# Verify blackout detection on a holiday date (exits 1)
bash scripts/deployment/check-freeze.sh --date 2026-12-25
```

---

## 4. Emergency P0 Hotfix Override Procedure

Freeze windows NEVER block mitigation of an active `P0` production outage or `CRITICAL` security vulnerability:

1. Confirm an active `P0` incident exists in `#incidents` ([Incident Severity Guide](incident-severity-guide.md)).
2. Obtain approval from the active **Incident Commander** AND **Engineering Team Lead / VP Engineering**.
3. Run the hardened deployment pipeline via GitHub Actions `workflow_dispatch` with:
   - `environment: production`
   - `emergency_hotfix: true`
   - `hotfix_reason: "INC-YYYYMMDD-HHMM: <concise justification>"`
4. Or validate locally with:
   ```bash
   bash scripts/deployment/check-freeze.sh --date 2026-12-25 \
     --emergency-override --reason "INC-20261225-0100: P0 auth hotfix"
   ```
5. Every freeze override requires a follow-up entry in the P0 blameless postmortem ([postmortem-template.md](postmortem-template.md)).

---

## Related Documentation

- [Hardened CI/CD Deployment Process](deployment-process.md)
- [Production Deployment Checklist](../runbooks/deployment-checklist.md)
- [Production SLOs & Error Budget Freeze Policy](slos.md)
- [Incident Management Process](incident-management.md)
