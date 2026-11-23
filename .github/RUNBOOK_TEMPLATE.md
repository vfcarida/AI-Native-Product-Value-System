# Runbook: [Incident Title]

**Severity**: P0 / P1 / P2 / P3  
**Alert**: `[AlertName]`  
**SLO Impact**: [SLO-01 Availability / SLO-02 Latency / SLO-03 Error Rate / SLO-04 Agent Success / Durability]  
**Estimated MTTR**: [X minutes]  
**Last Updated**: [YYYY-MM-DD]  
**Owner**: [Team]

---

## 1. Symptoms
- [ ] Prometheus alert fires in `#alerts-critical` or `#alerts-warnings`
- [ ] Observable user or telemetry impact

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# Confirm alert state and affected pods
kubectl get pods -n product-value-system -o wide
```

### Decision Tree
- Condition A -> Section 3.A
- Condition B -> Section 3.B

---

## 3. Remediation

### 3.A: Primary Mitigation
1. Execute remediation command.
2. Verify recovery via `/health/ready` and Prometheus.
3. **Rollback**: Document exact reversal command if mitigation causes adverse impact.

---

## 4. Prevention
- [ ] Structural fix, alerting refinement, or capacity adjustment

---

## 5. Escalation
If unresolved after 15 minutes, escalate via PagerDuty to Backup On-Call / Engineering Manager and post diagnosis findings in `#incidents`.

---

## 6. References
- [Troubleshooting Flowchart](../docs/runbooks/troubleshooting-flowchart.md)
- [On-Call Quick Reference](../docs/runbooks/oncall-quick-reference.md)

---

## 7. Testing
**Last Tested**: [YYYY-MM-DD]  
**Simulation**: Describe non-production validation command and measured MTTR.
