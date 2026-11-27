-- Migration 004 (SQLite): Durable async run job queue & SSE event stream log (v2.6)

CREATE TABLE IF NOT EXISTS run_jobs (
    job_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    job_type TEXT NOT NULL CHECK (job_type IN ('start', 'resume')),
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'leased', 'completed', 'failed', 'dead_letter')
    ),
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    worker_id TEXT,
    lease_expires_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_jobs_claim
    ON run_jobs(status, lease_expires_at, created_at);

CREATE INDEX IF NOT EXISTS idx_run_jobs_run_tenant
    ON run_jobs(run_id, tenant_id, created_at);

CREATE TABLE IF NOT EXISTS run_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    stage_name TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_events_run_tenant_seq
    ON run_events(run_id, tenant_id, seq);

INSERT OR IGNORE INTO schema_migrations (version) VALUES (4);
