-- Migration 003 (PostgreSQL): Durable multi-tenant runs & asynchronous HITL approvals (EVO-P3-05)

CREATE TABLE IF NOT EXISTS durable_runs (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    problem_id TEXT NOT NULL,
    problem_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled')
    ),
    current_stage TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    total_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    stages_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    artifacts_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_pg_durable_runs_tenant_status
    ON durable_runs(tenant_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS run_approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES durable_runs(run_id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_by TEXT,
    decided_at TIMESTAMPTZ,
    comment TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_pg_run_approvals_run_tenant
    ON run_approvals(run_id, tenant_id, status);

-- Enable PostgreSQL Row-Level Security (RLS) for defense-in-depth tenant isolation
ALTER TABLE durable_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_approvals ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'durable_runs' AND policyname = 'tenant_isolation_durable_runs'
    ) THEN
        CREATE POLICY tenant_isolation_durable_runs ON durable_runs
            USING (
                current_setting('app.current_tenant', true) IS NULL
                OR current_setting('app.current_tenant', true) = ''
                OR tenant_id = current_setting('app.current_tenant', true)
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'run_approvals' AND policyname = 'tenant_isolation_run_approvals'
    ) THEN
        CREATE POLICY tenant_isolation_run_approvals ON run_approvals
            USING (
                current_setting('app.current_tenant', true) IS NULL
                OR current_setting('app.current_tenant', true) = ''
                OR tenant_id = current_setting('app.current_tenant', true)
            );
    END IF;
END
$$;

INSERT INTO schema_migrations (version) VALUES (3) ON CONFLICT (version) DO NOTHING;
