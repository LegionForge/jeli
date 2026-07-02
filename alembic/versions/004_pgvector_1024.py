"""Embedding vectors become pgvector vector(1024) with an HNSW index.

1024 is the deliberate index standard: arctic-embed2 native, Qwen3-Embedding
MRL ceiling, OpenAI truncatable via the dimensions parameter. Model swaps are
re-embedding jobs (provenance columns exist for exactly that), never schema
migrations. Guarded: refuses on a non-empty table — with data present, write
the re-embedding migration instead.

Revision ID: 004_pgvector_1024
Revises: 003_key_id
"""

import sqlalchemy as sa
from alembic import op

revision = "004_pgvector_1024"
down_revision = "003_key_id"
branch_labels = None
depends_on = None

INDEX_DIMENSIONS = 1024


def upgrade() -> None:
    conn = op.get_bind()
    count = conn.execute(sa.text("SELECT COUNT(*) FROM memory_entry")).scalar()
    if count:
        raise RuntimeError(
            "memory_entry is not empty; write a re-embedding migration "
            "instead of running this one"
        )
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.drop_column("memory_entry", "embedding")
    op.execute(f"ALTER TABLE memory_entry ADD COLUMN embedding vector({INDEX_DIMENSIONS}) NOT NULL")
    op.drop_column("memory_entry", "delta_embedding")
    op.execute(f"ALTER TABLE memory_entry ADD COLUMN delta_embedding vector({INDEX_DIMENSIONS})")
    # cosine HNSW — the L1 hot-tier semantic index
    op.execute(
        "CREATE INDEX idx_memory_embedding_hnsw ON memory_entry "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_memory_embedding_hnsw")
    op.drop_column("memory_entry", "embedding")
    op.add_column(
        "memory_entry",
        sa.Column("embedding", sa.dialects.postgresql.JSONB(), nullable=False),
    )
    op.drop_column("memory_entry", "delta_embedding")
    op.add_column(
        "memory_entry",
        sa.Column("delta_embedding", sa.dialects.postgresql.JSONB(), nullable=True),
    )
