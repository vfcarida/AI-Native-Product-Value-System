-- Migration 001: Initial schema
-- Created: 2026-11-18

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Value stream runs
CREATE TABLE IF NOT EXISTS value_stream_runs (
    id TEXT PRIMARY KEY,
    problem_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled')),
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    outcome_achieved INTEGER,  -- NULL, 0 (false), 1 (true)
    artifact_dir TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON value_stream_runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON value_stream_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_runs_problem_id ON value_stream_runs(problem_id);

-- Stages (one per stage execution)
CREATE TABLE IF NOT EXISTS stages (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    agent_name TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    artifact_path TEXT,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    error TEXT,
    metadata TEXT,  -- JSON
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES value_stream_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_stages_run_id ON stages(run_id);
CREATE INDEX IF NOT EXISTS idx_stages_stage_name ON stages(stage_name);
CREATE INDEX IF NOT EXISTS idx_stages_agent_name ON stages(agent_name);

-- Policy decisions (authorization audit trail)
CREATE TABLE IF NOT EXISTS policy_decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    agent_name TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('allow', 'deny')),
    policy_matched TEXT,
    rationale TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES value_stream_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_policy_run_id ON policy_decisions(run_id);
CREATE INDEX IF NOT EXISTS idx_policy_decision ON policy_decisions(decision);
CREATE INDEX IF NOT EXISTS idx_policy_timestamp ON policy_decisions(timestamp);

-- Evaluations (judge results)
CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_id TEXT,
    artifact_type TEXT NOT NULL,
    judge_name TEXT NOT NULL,
    judge_type TEXT NOT NULL,  -- deterministic, ai_based, trajectory
    score REAL NOT NULL,
    passed INTEGER NOT NULL,  -- 0 or 1
    rationale TEXT,
    details TEXT,  -- JSON
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES value_stream_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (stage_id) REFERENCES stages(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_eval_run_id ON evaluations(run_id);
CREATE INDEX IF NOT EXISTS idx_eval_judge_name ON evaluations(judge_name);
CREATE INDEX IF NOT EXISTS idx_eval_passed ON evaluations(passed);

-- Costs (detailed cost tracking)
CREATE TABLE IF NOT EXISTS costs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_id TEXT,
    timestamp TIMESTAMP NOT NULL,
    agent_name TEXT,
    model_name TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES value_stream_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (stage_id) REFERENCES stages(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_costs_run_id ON costs(run_id);
CREATE INDEX IF NOT EXISTS idx_costs_agent_name ON costs(agent_name);
CREATE INDEX IF NOT EXISTS idx_costs_model_name ON costs(model_name);

-- Record migration
INSERT INTO schema_migrations (version) VALUES (1);
