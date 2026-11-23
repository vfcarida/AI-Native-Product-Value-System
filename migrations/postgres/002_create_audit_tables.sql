-- Migration 002: Append-only compliance audit_logs table (PostgreSQL, PROD-006)
-- Implements 14 compliance columns, JSONB metadata, 4 composite indexes,
-- immutability enforcement, and least-privilege GRANT/REVOKE statements.

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    event_id VARCHAR(36) NOT NULL UNIQUE,
    event_type VARCHAR(50) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    user_id VARCHAR(100),
    subject_id VARCHAR(100),
    action VARCHAR(500) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    resource_id VARCHAR(200),
    result VARCHAR(20) NOT NULL,
    metadata JSONB,
    client_ip VARCHAR(45),
    user_agent TEXT,
    session_id VARCHAR(100),
    signature VARCHAR(64) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_event_id ON audit_logs(event_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_subject_id ON audit_logs(subject_id);
CREATE INDEX IF NOT EXISTS idx_audit_resource_type ON audit_logs(resource_type);
CREATE INDEX IF NOT EXISTS idx_audit_session_id ON audit_logs(session_id);

-- Composite indexes for SOC2 investigations and GDPR DSAR queries
CREATE INDEX IF NOT EXISTS idx_audit_user_timestamp ON audit_logs(user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_subject_timestamp ON audit_logs(subject_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_type_timestamp ON audit_logs(event_type, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_logs(session_id, timestamp);

-- Enforce append-only permissions for the application service role (pvs_user)
-- Privileged archival/GDPR role manages 90-day S3 archival pruning separately.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pvs_user') THEN
        GRANT SELECT, INSERT ON audit_logs TO pvs_user;
        GRANT USAGE, SELECT ON SEQUENCE audit_logs_id_seq TO pvs_user;
        REVOKE DELETE, UPDATE, TRUNCATE ON audit_logs FROM pvs_user;
    END IF;
END
$$;

INSERT INTO schema_migrations (version) VALUES (2) ON CONFLICT (version) DO NOTHING;
