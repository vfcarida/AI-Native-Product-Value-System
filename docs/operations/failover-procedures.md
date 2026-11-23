# High Availability Failover & Zero-Downtime Procedures

Operational runbook for testing, executing, and rolling back High Availability failover across application pods, PgBouncer, and Patroni PostgreSQL clusters.

---

## 1. Inspecting Patroni & PgBouncer Health

```bash
# Check Patroni leader and streaming standby replication lag
kubectl exec -n product-value-system product-value-system-postgres-ha-0 -- patronictl list

# Inspect PgBouncer active pools and client/server ratios
kubectl exec -n product-value-system deploy/product-value-system-pgbouncer -- \
  psql -h localhost -p 5432 -U pvs_user -d pgbouncer -c "SHOW POOLS;"

# Check HPA, PDB, and cross-node pod spread
kubectl get hpa,pdb -n product-value-system
kubectl get pods -n product-value-system -o wide
```

---

## 2. Automated Verification Suites

- **PostgreSQL Failover Drill (`< 30s` RTO)**: `bash tests/resilience/test_failover.sh`
- **Zero-Downtime Rolling Deployment (`0` failed requests)**: `bash tests/resilience/test_zero_downtime.sh`
- **Python Resilience & Load-Distribution Suite**: `pytest tests/resilience/test_load_distribution.py -v`

---

## 3. Manual Patroni Switchover (Planned Maintenance)

```bash
kubectl exec -n product-value-system product-value-system-postgres-ha-0 -- \
  patronictl switchover product-value-system-pg-cluster
```

---

## 4. Handling PDB During Planned Node Drains

If `PodDisruptionBudget` (`minAvailable: 2`) blocks a node drain at `3` replicas while another pod is restarting:
1. Temporarily scale to `4` replicas (`kubectl scale deployment product-value-system --replicas=4 -n product-value-system`) so `ALLOWED DISRUPTIONS` becomes `2`.
2. Drain the node cleanly; HPA scales back down after the `300s` stabilization window.

---

## 5. Rollback to Single-Replica Mode

```bash
helm upgrade product-value-system deploy/helm/product-value-system \
  --set ha.enabled=false \
  --set replicaCount=1 \
  --set autoscaling.enabled=false \
  --set podDisruptionBudget.enabled=false \
  --set postgresql.ha.enabled=false \
  --set pgbouncer.enabled=false
```
