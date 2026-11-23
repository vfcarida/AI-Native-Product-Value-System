# Chaos Engineering GameDay Facilitation Guide

**Owner**: SRE Team  
**Cadence**: Monthly (Last Friday, 14:00–16:00 UTC)  
**Last Updated**: 2026-11-23

---

## 1. Purpose & Safety Invariants

Chaos Engineering GameDays proactively inject controlled failures into our Kubernetes infrastructure to surface hidden weaknesses, validate runbooks, and build team muscle memory before real production outages happen.

### Non-Negotiable Safety Guardrails
1. **Staging First**: All GameDays execute in `product-value-system-staging` by default. Production chaos injection is **prohibited** without explicit VP Engineering sign-off, an approved change ticket, and `--allow-production --approval-ticket <ID>`.
2. **Instant Kill Switch**: Every chaos automation script in `scripts/gameday/` supports `--cleanup` to remove Chaos Mesh CRDs (`PodChaos`, `NetworkChaos`, `StressChaos`) within seconds.
3. **Abort Conditions**: Halt immediately and run `--cleanup` if blast radius crosses namespace boundaries, staging data corruption occurs, or a real production P0/P1 incident fires simultaneously.
4. **Blameless Learning**: GameDays measure **system resilience and runbook accuracy**, never individual engineer speed or performance.
5. **Audit Trail**: Every GameDay is logged in a GitHub issue labeled `gameday` capturing scenario ID, participants, measured SLOs, outcome (`Green`/`Yellow`/`Red`), and Kaizen follow-up tickets.

---

## 2. GameDay Team Roles (Rotate Monthly)

| Role | Responsibility |
|---|---|
| **Facilitator** | Keeps time, reads scenario phases, enforces abort conditions, leads retrospective |
| **Chaos Injector** | Runs `scripts/gameday/*.sh` (first with `--dry-run`, then live) and owns immediate `--cleanup` |
| **Practice Incident Commander (IC)** | Coordinates responders using [Incident Commander Guide](incident-commander-guide.md) and maintains UTC timeline |
| **Responders (2–3)** | Diagnose using Grafana, Jaeger, `kubectl`, and linked runbooks (`docs/runbooks/incident-*.md`) |
| **Observer / Scribe** | Records alert firing delays, SLO graphs, surprises, and Kaizen action items |

---

## 3. Standard 2-Hour GameDay Agenda

| Time | Phase | Activities |
|---|---|---|
| `00–15 min` | **Briefing & Safety Check** | Verify staging `/health/ready`, open Grafana (`pvs-slo-dashboard`, `pvs-alert-metrics`) & Jaeger, confirm `--cleanup` command ready, announce start in `#gameday` |
| `15–25 min` | **Baseline Capture** | Record baseline error rate (`< 1%`), API p99 (`< 1s`), ready replicas (`3/3`), and start synthetic load (`k6`) |
| `25–65 min` | **Chaos Injection & Response** | Execute scenario from [GameDay Scenario Catalog](gameday-scenarios/README.md); Practice IC coordinates mitigation following runbook |
| `65–80 min` | **Recovery & Cleanup** | Run `--cleanup`, verify all pods `Ready`, confirm metrics & alerts return to baseline |
| `80–110 min` | **Blameless Retrospective** | Compare observed vs expected outcomes (`Green`/`Yellow`/`Red`), identify alert/runbook/architecture gaps |
| `110–120 min` | **Kaizen Capture** | Close GameDay tracking issue and promote every discovered gap to [.github/projects/kaizen-board.yml](../../.github/projects/kaizen-board.yml) |

---

## 4. Chaos Mesh Prerequisites & RBAC

```bash
# Install Chaos Mesh in isolated namespace (staging cluster)
helm repo add chaos-mesh https://charts.chaos-mesh.org
helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh \
  --namespace chaos-mesh --create-namespace \
  --set chaosDaemon.runtime=containerd \
  --set dashboard.securityMode=true

# Verify Chaos Mesh controllers are healthy
kubectl get pods -n chaos-mesh
```

Restrict `chaos-mesh.org` RBAC (`PodChaos`, `NetworkChaos`, `StressChaos`) exclusively to the `sre-gameday` ServiceAccount/Role bound to `product-value-system-staging`.

---

## 5. Outcome Scoring & Kaizen Promotion

- **Green (Pass)**: Error rate `< 1%`, latency p99 `< 2s`, automatic recovery inside target window, runbook accurate.
- **Yellow (Partial)**: Brief SLO dip or slow recovery; create `P1`/`P2` Kaizen items for tuning.
- **Red (Fail)**: Sustained outage, failed failover, PDB violation, or silent alert miss; create `P0`/`P1` Kaizen items and re-run scenario next month to verify remediation.

---

## Related Documentation

- [GameDay Scenario Catalog (15 Scenarios)](gameday-scenarios/README.md)
- [SRE Team Rituals](team-rituals.md)
- [Improvement Metrics (DORA & SPACE)](improvement-metrics.md)
- [Quarterly DR Drill Procedures](dr-drill-procedures.md)
- [Incident Commander Guide](incident-commander-guide.md)
