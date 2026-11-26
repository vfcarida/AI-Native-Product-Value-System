-- Migration 003: Durable multi-tenant value stream runs & asynchronous HITL approvals (EVO-P3-05)

CREATE TABLE IF NOT EXISTS durable_runs (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    problem_id TEXT NOT NULL,
    problem_payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled')
    ),
    current_stage TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    stages_json TEXT NOT NULL DEFAULT '{}',
    artifacts_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_durable_runs_tenant_status
    ON durable_runs(tenant_id, status, created_at);

CREATE TABLE IF NOT EXISTS run_approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    requested_at TEXT NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    comment TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (run_id) REFERENCES durable_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_run_approvals_run_tenant
    ON run_approvals(run_id, tenant_id, status);

INSERT OR IGNORE INTO schema_migrations (version) VALUES (3);
