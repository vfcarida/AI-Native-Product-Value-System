# Feature Flag Management Guide (EVO-015)

Feature flags in the AI-Native Product Value System are stored declaratively in `flags.yaml`, validated against [`schemas/feature_flags.schema.json`](../schemas/feature_flags.schema.json), and managed through Git, the `pvs flags` CLI, or the FastAPI Admin UI.

---

## Flag Taxonomy (Martin Fowler)

| Type | Lifecycle | Typical Use |
| :--- | :--- | :--- |
| `release` | Short-to-medium (remove after `full` stabilization) | Incremental rollout of new prompts, agents, or multi-agent review |
| `experiment` | Temporary (remove after experiment conclusion) | A/B comparison of model prompts or output formats |
| `ops` | Long-lived | Emergency kill switches (`cost_kill_switch`), circuit breakers |
| `permission` | Long-lived | Early-access / tier entitlements per `user_id` |

---

## Defining Flags in `flags.yaml`

```yaml
flags:
  - name: new_discovery_prompt
    description: "Enhanced discovery agent prompt with TinyTroupe simulation"
    type: release
    enabled: true
    rollout_stage: canary
    rollout_percentage: 0.05
    targeting_rules:
      - type: user_id
        operator: in
        values: ["user-123", "user-456"]
      - type: percentage
        value: 0.05
    created_at: "2026-11-20T10:00:00Z"
    updated_at: "2026-11-20T10:00:00Z"
```

### Supported Targeting Rules

Rules are evaluated top-to-bottom after confirming `enabled: true`:

1. **`user_id`**: Operators `in`, `not_in`, `equals`.
2. **`environment`**: Matches `request_context["environment"]` (`equals`, `not_equals`, `in`).
3. **`attribute`**: Matches arbitrary `request_context[attribute]` keys (`equals`, `not_equals`, `in`, `not_in`).
4. **`percentage`**: Consistent SHA-256 bucketing on `f"{flag_name}:{user_id}"` modulo `10000` (0.01% granularity).

If no targeting rule matches, evaluation falls back to `rollout_percentage` using the same consistent SHA-256 hash so users never flip between variants.

---

## Managing Flags via CLI

```bash
# List all configured flags
pvs flags list

# Toggle a flag's master switch on/off
pvs flags toggle cost_kill_switch --actor oncall-sre

# Adjust rollout percentage or stage
pvs flags rollout new_discovery_prompt --stage staged_25 --percentage 0.25

# Dry-run evaluation for a specific user and environment
pvs flags evaluate multi_agent_review --user user-123 --env staging

# Advance a flag to the next progressive stage (runs SLO quality gates first)
pvs flags advance multi_agent_review
```

---

## Managing Flags via Admin UI & Docker Compose

Start the monitoring + feature flag admin stack:

```bash
docker compose -f docker-compose.monitoring.yml up -d
```

- **HTML Dashboard**: `http://localhost:8080/admin/flags`
- **Flag Detail Page**: `http://localhost:8080/admin/flags/<flag_name>`
- **JSON API**: `GET /admin/api/flags`, `POST /admin/flags/<name>/toggle`, `POST /admin/flags/<name>/rollout`, `POST /admin/flags/<name>/advance`, `POST /admin/flags/<name>/rollback`, `GET /admin/audit`

### Security & Audit Trail

- Set `PVS_ADMIN_API_KEY` in production to require `X-API-Key` or `Authorization: Bearer <token>` on all mutating (`POST`) and `/admin/audit` endpoints.
- Every mutation (toggle, rollout update, stage advance, rollback) records an immutable `AuditEntry` with actor, previous state, new state, and UTC timestamp.
- All loads and saves enforce JSON Schema validation to block malformed or injected flag rules.

---

## Flag Lifecycle & Hygiene

1. **Create**: Add flag to `flags.yaml` in `dark` stage (`rollout_percentage: 0.0`).
2. **Roll out**: Progress through `canary` -> `staged_5` -> `staged_25` -> `staged_50` -> `full` via `RolloutController`.
3. **Retire**: Once a `release` or `experiment` flag has been at `full` (100%) for 14+ days without incidents, remove the conditional branch from code and delete the flag entry from `flags.yaml` (keep `ops` kill switches permanently).

---

## Related Documentation

- [Progressive Delivery Guide](progressive-delivery.md)
- [Developer Flag Integration Guide](developer/flag-integration.md)
- [SLO & Error Budget Guide](slo-guide.md)
