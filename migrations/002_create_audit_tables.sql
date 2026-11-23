-- Migration 002: Append-only compliance audit_logs table (SQLite, PROD-006)
-- Enforces immutability via BEFORE UPDATE and BEFORE DELETE triggers.

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    user_id TEXT,
    subject_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    result TEXT NOT NULL,
    metadata TEXT,
    client_ip TEXT,
    user_agent TEXT,
    session_id TEXT,
    signature TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_event_id ON audit_logs(event_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_subject_id ON audit_logs(subject_id);
CREATE INDEX IF NOT EXISTS idx_audit_resource_type ON audit_logs(resource_type);
CREATE INDEX IF NOT EXISTS idx_audit_session_id ON audit_logs(session_id);

-- Composite indexes for compliance & GDPR DSAR search queries
CREATE INDEX IF NOT EXISTS idx_audit_user_timestamp ON audit_logs(user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_subject_timestamp ON audit_logs(subject_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_type_timestamp ON audit_logs(event_type, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_logs(session_id, timestamp);

-- Database-level immutability triggers (append-only enforcement)
CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_update
BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit_logs is append-only: UPDATE is prohibited');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_delete
BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit_logs is append-only: DELETE is prohibited');
END;

INSERT OR IGNORE INTO schema_migrations (version) VALUES (2);
