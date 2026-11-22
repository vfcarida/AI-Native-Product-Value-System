-- Migration 001: Initial PostgreSQL schema (multi-node async persistence, v1.1)
-- Mirrors migrations/001_initial_schema.sql using native PostgreSQL types & JSONB.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS value_stream_runs (
    id TEXT PRIMARY KEY,
    problem_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled')),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    total_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    outcome_achieved BOOLEAN,
    artifact_dir TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pg_runs_status ON value_stream_runs(status);
CREATE INDEX IF NOT EXISTS idx_pg_runs_started_at ON value_stream_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_runs_problem_id ON value_stream_runs(problem_id);

CREATE TABLE IF NOT EXISTS stages (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES value_stream_runs(id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    agent_name TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    artifact_path TEXT,
    cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    error TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pg_stages_run_id ON stages(run_id);
CREATE INDEX IF NOT EXISTS idx_pg_stages_stage_name ON stages(stage_name);

CREATE TABLE IF NOT EXISTS policy_decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES value_stream_runs(id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ NOT NULL,
    agent_name TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('allow', 'deny')),
    policy_matched TEXT,
    rationale TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pg_policy_run_id ON policy_decisions(run_id);
CREATE INDEX IF NOT EXISTS idx_pg_policy_decision ON policy_decisions(decision);

CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES value_stream_runs(id) ON DELETE CASCADE,
    stage_id TEXT REFERENCES stages(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    judge_name TEXT NOT NULL,
    judge_type TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    passed BOOLEAN NOT NULL,
    rationale TEXT,
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pg_eval_run_id ON evaluations(run_id);

CREATE TABLE IF NOT EXISTS costs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES value_stream_runs(id) ON DELETE CASCADE,
    stage_id TEXT REFERENCES stages(id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ NOT NULL,
    agent_name TEXT,
    model_name TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pg_costs_run_id ON costs(run_id);

INSERT INTO schema_migrations (version) VALUES (1) ON CONFLICT (version) DO NOTHING;
