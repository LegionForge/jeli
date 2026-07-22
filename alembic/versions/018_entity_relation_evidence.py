"""018 — Attribute entity relations to the memories that asserted them.

Revision ID: 018_entity_relation_evidence
Revises: 017_fix_column_grant_gaps
Create Date: 2026-07-11
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "018_entity_relation_evidence"
down_revision = "017_fix_column_grant_gaps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity_relation_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column("relation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["relation_id"], ["entity_relation.id"]),
        sa.ForeignKeyConstraint(["memory_id"], ["memory_entry.id"]),
        sa.UniqueConstraint("relation_id", "memory_id", name="uq_relation_evidence_memory"),
    )
    op.create_index(
        "idx_relation_evidence_relation", "entity_relation_evidence", ["relation_id"]
    )
    op.create_index(
        "idx_relation_evidence_memory", "entity_relation_evidence", ["memory_id"]
    )

    op.execute("REVOKE UPDATE, DELETE ON entity_relation_evidence FROM jeli_app")
    op.execute("GRANT SELECT, INSERT ON entity_relation_evidence TO jeli_app")


def downgrade() -> None:
    op.drop_table("entity_relation_evidence")
