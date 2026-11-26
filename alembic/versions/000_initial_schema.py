"""Create initial value stream runs, stages, policy_decisions, evaluations, and costs tables.

Revision ID: 001_initial_schema
Revises: None
Create Date: 2026-11-18 10:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "value_stream_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("problem_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("outcome_achieved", sa.Boolean()),
        sa.Column("artifact_dir", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_runs_status", "value_stream_runs", ["status"])
    op.create_index("idx_runs_started_at", "value_stream_runs", ["started_at"])
    op.create_index("idx_runs_problem_id", "value_stream_runs", ["problem_id"])

    op.create_table(
        "stages",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("value_stream_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage_name", sa.Text(), nullable=False),
        sa.Column("agent_name", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("artifact_path", sa.Text()),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("error", sa.Text()),
        sa.Column("metadata", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_stages_run_id", "stages", ["run_id"])
    op.create_index("idx_stages_stage_name", "stages", ["stage_name"])

    op.create_table(
        "policy_decisions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("value_stream_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("policy_matched", sa.Text()),
        sa.Column("rationale", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_policy_run_id", "policy_decisions", ["run_id"])

    op.create_table(
        "evaluations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("value_stream_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage_id", sa.Text(), sa.ForeignKey("stages.id", ondelete="CASCADE")),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("judge_name", sa.Text(), nullable=False),
        sa.Column("judge_type", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("rationale", sa.Text()),
        sa.Column("details", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_eval_run_id", "evaluations", ["run_id"])

    op.create_table(
        "costs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("value_stream_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage_id", sa.Text(), sa.ForeignKey("stages.id", ondelete="CASCADE")),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent_name", sa.Text()),
        sa.Column("model_name", sa.Text()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_costs_run_id", "costs", ["run_id"])


def downgrade() -> None:
    op.drop_table("costs")
    op.drop_table("evaluations")
    op.drop_table("policy_decisions")
    op.drop_table("stages")
    op.drop_table("value_stream_runs")
