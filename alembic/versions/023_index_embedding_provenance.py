"""023 — separate mutable index provenance from record provenance.

The original embedding model, dimensions, and timestamp are canonical record
facts. Re-embedding is retrieval-cache maintenance, so its current provenance
needs separate columns. They are nullable during the rolling transition: old
writers produce an all-null triple, while existing rows are backfilled.

Revision ID: 023_index_embedding_provenance
Revises: 022_canonical_versions
Create Date: 2026-09-09
"""

import sqlalchemy as sa

from alembic import op

revision = "023_index_embedding_provenance"
down_revision = "022_canonical_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep live/archive append order identical: archival uses positional copy.
    op.add_column(
        "memory_entry",
        sa.Column(
            "index_embedding_model",
            sa.Text(),
            nullable=True,
        ),
    )
    op.add_column(
        "memory_entry",
        sa.Column(
            "index_embedding_dimensions",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "memory_entry",
        sa.Column(
            "index_embedded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "memory_archive",
        sa.Column(
            "index_embedding_model",
            sa.Text(),
            nullable=True,
        ),
    )
    op.add_column(
        "memory_archive",
        sa.Column(
            "index_embedding_dimensions",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "memory_archive",
        sa.Column(
            "index_embedded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    for table in ("memory_entry", "memory_archive"):
        op.execute(f"""
            UPDATE {table}
            SET index_embedding_model = embedding_model,
                index_embedding_dimensions = embedding_dimensions,
                index_embedded_at = embedded_at
        """)
        op.create_check_constraint(
            f"ck_{table}_index_embedding_provenance",
            table,
            "(index_embedding_model IS NULL "
            "AND index_embedding_dimensions IS NULL "
            "AND index_embedded_at IS NULL) OR "
            "(index_embedding_model IS NOT NULL "
            "AND index_embedding_dimensions IS NOT NULL "
            "AND index_embedded_at IS NOT NULL)",
        )


def downgrade() -> None:
    # Once runtime writes this provenance, dropping it would leave the mutable
    # vector without an accountable model/dimensions/time triple.
    pass
