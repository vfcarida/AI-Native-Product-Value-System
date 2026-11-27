"""Create durable async run_jobs queue and run_events SSE log tables (v2.6).

Revision ID: d4e5f6071829
Revises: c3d4e5f60718
Create Date: 2026-11-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d4e5f6071829"
down_revision: str | None = "c3d4e5f60718"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")

    op.create_table(
        "run_jobs",
        sa.Column("job_id", sa.String(length=64), primary_key=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", json_type, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_run_jobs_claim",
        "run_jobs",
        ["status", "lease_expires_at", "created_at"],
    )
    op.create_index(
        "idx_run_jobs_run_tenant",
        "run_jobs",
        ["run_id", "tenant_id", "created_at"],
    )

    op.create_table(
        "run_events",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("stage_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("payload_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_run_events_run_tenant_seq",
        "run_events",
        ["run_id", "tenant_id", "seq"],
    )

    if dialect == "postgresql":
        op.execute("ALTER TABLE run_jobs ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE run_events ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("idx_run_events_run_tenant_seq", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("idx_run_jobs_run_tenant", table_name="run_jobs")
    op.drop_index("idx_run_jobs_claim", table_name="run_jobs")
    op.drop_table("run_jobs")
