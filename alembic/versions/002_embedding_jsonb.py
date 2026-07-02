"""Store embedding vectors as JSONB (Phase 1).

001 created `embedding`/`delta_embedding` as UUID placeholders. Phase 1
stores the actual vector as JSONB so writes carry full embedding provenance
without requiring the pgvector extension; a later migration converts to
vector(N) and adds the HNSW index for semantic search.

Revision ID: 002_embedding_jsonb
Revises: 001_initial_jeli_schema
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "002_embedding_jsonb"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # UUID → JSONB is not castable; the table is empty pre-Phase-1, so
    # drop-and-recreate is safe. Guarded by a row-count check anyway.
    conn = op.get_bind()
    count = conn.execute(sa.text("SELECT COUNT(*) FROM memory_entry")).scalar()
    if count:
        raise RuntimeError(
            "memory_entry is not empty; write a data-preserving migration "
            "instead of running this one"
        )
    op.drop_column("memory_entry", "embedding")
    op.add_column(
        "memory_entry",
        sa.Column("embedding", postgresql.JSONB(), nullable=False),
    )
    op.drop_column("memory_entry", "delta_embedding")
    op.add_column(
        "memory_entry",
        sa.Column("delta_embedding", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("memory_entry", "embedding")
    op.add_column(
        "memory_entry",
        sa.Column("embedding", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.drop_column("memory_entry", "delta_embedding")
    op.add_column(
        "memory_entry",
        sa.Column("delta_embedding", postgresql.UUID(as_uuid=True), nullable=True),
    )
