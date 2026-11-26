"""Create multi-tenant durable_runs and run_approvals tables for async HITL (EVO-P3-05).

Revision ID: b2c3d4e5f607
Revises: a1b2c3d4e5f6
Create Date: 2026-11-26 10:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f607"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "durable_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("problem_id", sa.Text(), nullable=False),
        sa.Column("problem_payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_stage", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("stages_json", sa.JSON(), nullable=False),
        sa.Column("artifacts_json", sa.JSON(), nullable=False),
    )
    op.create_index(
        "idx_durable_runs_tenant_status",
        "durable_runs",
        ["tenant_id", "status", "created_at"],
    )

    op.create_table(
        "run_approvals",
        sa.Column("approval_id", sa.Text(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("durable_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("stage_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index(
        "idx_run_approvals_run_tenant",
        "run_approvals",
        ["run_id", "tenant_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_run_approvals_run_tenant", table_name="run_approvals")
    op.drop_table("run_approvals")
    op.drop_index("idx_durable_runs_tenant_status", table_name="durable_runs")
    op.drop_table("durable_runs")
