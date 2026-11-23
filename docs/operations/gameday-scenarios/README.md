# Chaos Engineering GameDay Scenario Catalog

**Owner**: SRE Team  
**Facilitation Guide**: [GameDay Facilitation Guide](../gameday-facilitation-guide.md)  
**Cadence**: Monthly (Last Friday, 14:00–16:00 UTC) in `product-value-system-staging`

---

## Difficulty Levels

- **Easy**: Single component failure, well-documented recovery (`30–45 min`)
- **Medium**: Multi-component interaction, failover, or resource pressure (`60–90 min`)
- **Hard**: Cascading, network partition, or multi-AZ failure modes (`90–120 min`)

---

## Complete Scenario Catalog (15 Scenarios)

### High Availability (HA)
| ID | Scenario | Difficulty | Automation / Tool | Linked Runbook |
|---|---|---|---|---|
| `GAMEDAY-001` | [01: Single Pod Failure](01-pod-failure.md) | Easy | `scripts/gameday/kill-random-pod.sh` | [Pod CrashLoop](../../runbooks/incident-pod-crashloop.md) |
| `GAMEDAY-002` | [02: Multiple Pod Failures (PDB Test)](02-multiple-pod-failures.md) | Medium | `scripts/gameday/kill-random-pod.sh` | [API Unavailable](../../runbooks/incident-api-unavailable.md) |
| `GAMEDAY-003` | [03: Worker Node Failure](03-node-failure.md) | Medium | `kubectl cordon` / `drain` | [High Availability](../ha-architecture.md) |
| `GAMEDAY-004` | [04: Availability Zone Failure](04-az-failure.md) | Hard | Zone taint / topology spread test | [High Availability](../ha-architecture.md) |

### Database Resilience
| ID | Scenario | Difficulty | Automation / Tool | Linked Runbook |
|---|---|---|---|---|
| `GAMEDAY-011` | [05: Database Primary Failure](05-database-primary-failure.md) | Medium | `kubectl delete pod postgresql-ha-0` | [DB Primary Down](../../runbooks/incident-database-primary-down.md) |
| `GAMEDAY-012` | [06: Database Connection Exhaustion](06-database-connection-exhaustion.md) | Medium | PgBouncer saturation + `k6` | [DB Connection Exhaustion](../../runbooks/incident-database-connection-exhaustion.md) |
| `GAMEDAY-013` | [07: Database Slow Queries](07-database-slow-queries.md) | Medium | `pg_sleep` + `diagnose-slow-queries.sh` | [Slow Queries](../../runbooks/incident-slow-queries.md) |

### Network Resilience
| ID | Scenario | Difficulty | Automation / Tool | Linked Runbook |
|---|---|---|---|---|
| `GAMEDAY-021` | [08: Network Latency Injection](08-network-latency-injection.md) | Easy | `scripts/gameday/inject-latency.sh` | [High API Latency](../../runbooks/incident-high-latency.md) |
| `GAMEDAY-022` | [09: Network Partition (Split-Brain Guard)](09-network-partition.md) | Hard | `scripts/gameday/partition-network.sh` | [Replication Lag](../../runbooks/incident-replication-lag.md) |
| `GAMEDAY-023` | [10: DNS Resolution Failure](10-dns-failure.md) | Medium | Chaos Mesh `DNSChaos` | [Sandbox Egress Block](../../runbooks/incident-sandbox-egress-block.md) |

### Resource Exhaustion
| ID | Scenario | Difficulty | Automation / Tool | Linked Runbook |
|---|---|---|---|---|
| `GAMEDAY-031` | [11: Memory Exhaustion & OOM Handling](11-memory-exhaustion.md) | Medium | `scripts/gameday/exhaust-memory.sh` | [Pod OOM Kill](../../runbooks/incident-oom-kill.md) |
| `GAMEDAY-032` | [12: CPU Saturation & HPA Scale-Out](12-cpu-saturation.md) | Medium | Chaos Mesh CPU stress + `k6` | [High CPU Usage](../../runbooks/incident-high-cpu-usage.md) |
| `GAMEDAY-033` | [13: Disk Space Exhaustion](13-disk-full.md) | Medium | Staging PVC fill + expansion | [Disk Full](../../runbooks/incident-disk-full.md) |

### External LLM Dependencies
| ID | Scenario | Difficulty | Automation / Tool | Linked Runbook |
|---|---|---|---|---|
| `GAMEDAY-041` | [14: OpenAI API Outage & Provider Failover](14-openai-api-outage.md) | Medium | `NetworkChaos` / mock `503` | [LLM Provider Outage](../../runbooks/incident-llm-provider-outage.md) |
| `GAMEDAY-042` | [15: Anthropic API High Latency & Circuit Breaker](15-anthropic-api-latency.md) | Medium | `NetworkChaos` delay (`5s`) | [LLM Rate Limit](../../runbooks/incident-llm-rate-limit.md) |

---

## 2027 Monthly GameDay Schedule

- **Jan 29**: `GAMEDAY-001` — Single Pod Failure
- **Feb 26**: `GAMEDAY-011` — Database Primary Failure (Patroni Failover)
- **Mar 26**: `GAMEDAY-021` — Network Latency Injection
- **Apr 30**: `GAMEDAY-031` — Memory Exhaustion & OOM Handling
- **May 28**: `GAMEDAY-041` — OpenAI API Outage & Multi-Provider Failover
- **Jun 25**: `GAMEDAY-002` — Multiple Pod Failures (PDB Validation)
- **Jul 30**: `GAMEDAY-022` — Network Partition (Split-Brain Guard)
- **Aug 27**: `GAMEDAY-003` — Worker Node Failure
- **Sep 24**: `GAMEDAY-032` — CPU Saturation & HPA Scale-Out
- **Oct 29**: `GAMEDAY-013` — Database Slow Queries
- **Nov 19**: `GAMEDAY-004` — Availability Zone Failure *(scheduled ahead of Thanksgiving freeze)*
- **December**: No GameDay (Year-End Holiday Freeze per [Deployment Freeze Calendar](../deployment-freeze-calendar.md))
