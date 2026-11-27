"""Create append-only audit_logs table for compliance (SOC2, GDPR, HIPAA — PROD-006).

Revision ID: a1b2c3d4e5f6
Revises: 001_initial_schema
Create Date: 2026-11-23 10:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "a1b2c3d4e5f6"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(36), unique=True, nullable=False, index=True),
        sa.Column("event_type", sa.String(50), nullable=False, index=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("user_id", sa.String(100), index=True),
        sa.Column("subject_id", sa.String(100), index=True),
        sa.Column("action", sa.String(500), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False, index=True),
        sa.Column("resource_id", sa.String(200)),
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("metadata", sa.JSON().with_variant(JSONB(), "postgresql")),
        sa.Column("client_ip", sa.String(45)),
        sa.Column("user_agent", sa.Text()),
        sa.Column("session_id", sa.String(100), index=True),
        sa.Column("signature", sa.String(64), nullable=False),
    )

    op.create_index("idx_audit_user_timestamp", "audit_logs", ["user_id", "timestamp"])
    op.create_index("idx_audit_subject_timestamp", "audit_logs", ["subject_id", "timestamp"])
    op.create_index("idx_audit_type_timestamp", "audit_logs", ["event_type", "timestamp"])
    op.create_index("idx_audit_session", "audit_logs", ["session_id", "timestamp"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pvs_user') THEN
                    GRANT SELECT, INSERT ON audit_logs TO pvs_user;
                    REVOKE DELETE, UPDATE, TRUNCATE ON audit_logs FROM pvs_user;
                END IF;
            END
            $$;
            """
        )


def downgrade() -> None:
    op.drop_index("idx_audit_session", table_name="audit_logs")
    op.drop_index("idx_audit_type_timestamp", table_name="audit_logs")
    op.drop_index("idx_audit_subject_timestamp", table_name="audit_logs")
    op.drop_index("idx_audit_user_timestamp", table_name="audit_logs")
    op.drop_table("audit_logs")
