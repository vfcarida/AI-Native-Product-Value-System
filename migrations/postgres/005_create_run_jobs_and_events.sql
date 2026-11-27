-- Migration 005 (PostgreSQL): Durable async run job queue (SKIP LOCKED) & SSE event log (v2.6)

CREATE TABLE IF NOT EXISTS run_jobs (
    job_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    job_type TEXT NOT NULL CHECK (job_type IN ('start', 'resume')),
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'leased', 'completed', 'failed', 'dead_letter')
    ),
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    worker_id TEXT,
    lease_expires_at TIMESTAMPTZ,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pg_run_jobs_claim
    ON run_jobs(status, lease_expires_at, created_at);

CREATE INDEX IF NOT EXISTS idx_pg_run_jobs_run_tenant
    ON run_jobs(run_id, tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS run_events (
    seq BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    stage_name TEXT NOT NULL DEFAULT '',
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pg_run_events_run_tenant_seq
    ON run_events(run_id, tenant_id, seq ASC);

-- Multi-tenant Row Level Security (RLS) aligned with migrations 003 & 004
ALTER TABLE run_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_events ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'run_jobs' AND policyname = 'tenant_isolation_run_jobs'
    ) THEN
        CREATE POLICY tenant_isolation_run_jobs ON run_jobs
            USING (
                current_setting('app.current_tenant', true) IS NULL
                OR current_setting('app.current_tenant', true) = ''
                OR tenant_id = current_setting('app.current_tenant', true)
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'run_events' AND policyname = 'tenant_isolation_run_events'
    ) THEN
        CREATE POLICY tenant_isolation_run_events ON run_events
            USING (
                current_setting('app.current_tenant', true) IS NULL
                OR current_setting('app.current_tenant', true) = ''
                OR tenant_id = current_setting('app.current_tenant', true)
            );
    END IF;
END
$$;

INSERT INTO schema_migrations (version) VALUES (5) ON CONFLICT (version) DO NOTHING;
