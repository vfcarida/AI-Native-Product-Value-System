"""Create pgvector HNSW temporal semantic cache and layered memory tables (EVO-P3-10 / EVO-P3-12).

Revision ID: c3d4e5f60718
Revises: b2c3d4e5f607
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f60718"
down_revision: str | None = "b2c3d4e5f607"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "semantic_cache_entries",
        sa.Column("cache_key", sa.String(length=128), primary_key=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=False, server_default="default"),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("cached_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "idx_sem_cache_tenant_model_ver",
        "semantic_cache_entries",
        ["tenant_id", "model", "prompt_version", "valid_until"],
    )

    op.create_table(
        "semantic_memory_chunks",
        sa.Column("chunk_id", sa.String(length=128), primary_key=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=False, server_default="default"),
        sa.Column("layer", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_sem_memory_tenant_layer",
        "semantic_memory_chunks",
        ["tenant_id", "layer", "created_at"],
    )

    if dialect == "postgresql":
        op.execute("ALTER TABLE semantic_cache_entries ADD COLUMN embedding vector(1536)")
        op.execute("ALTER TABLE semantic_memory_chunks ADD COLUMN embedding vector(1536)")
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_sem_cache_embedding_hnsw "
            "ON semantic_cache_entries USING hnsw (embedding vector_cosine_ops)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_sem_memory_embedding_hnsw "
            "ON semantic_memory_chunks USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    op.drop_index("idx_sem_memory_tenant_layer", table_name="semantic_memory_chunks")
    op.drop_table("semantic_memory_chunks")
    op.drop_index("idx_sem_cache_tenant_model_ver", table_name="semantic_cache_entries")
    op.drop_table("semantic_cache_entries")
