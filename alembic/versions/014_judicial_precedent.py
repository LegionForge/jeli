"""014 — Judicial Precedent: settled case law + human-escalation queue.

The Judicial branch resolved conflicts ad-hoc (higher trust wins, newer wins on
tie) with no memory of past rulings. This adds two tables that give it case law:

  judicial_precedent — one row per conflict *pattern* (a hash of the two memory
      types + contradiction type). The resolver looks a pattern up before
      re-deliberating; once a precedent's confidence crosses the apply
      threshold it is reinforced instead of re-derived. Append-mostly:
      applied_count / confidence / last_applied_at are the only mutable columns.

  judicial_human_queue — conflicts the resolver could not settle (e.g. a MEDIUM
      contradiction re-flagged repeatedly) surface here for the user. Resolving
      an entry stamps resolved_at / resolution / resolved_by; rows are never
      deleted, mirroring the append-only discipline of the rest of the store.

jeli_app grants follow the per-table rule from 010: SELECT + INSERT for both,
column-scoped UPDATE for the mutable counters, and no DELETE on either table.

Revision ID: 014_judicial_precedent
Revises: 013_constitutional_layer
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "014_judicial_precedent"
down_revision = "013_constitutional_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "judicial_precedent",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column("contradiction_type", sa.Text(), nullable=False),
        sa.Column("pattern_hash", sa.Text(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column("winner_rule", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column("applied_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "first_set_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pattern_hash", name="uq_judicial_precedent_pattern_hash"),
        sa.CheckConstraint(
            "resolution IN ('trust_wins','newer_wins','user_escalated','custom')",
            name="ck_judicial_precedent_resolution",
        ),
    )

    op.create_table(
        "judicial_human_queue",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column("memory_id_a", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_id_b", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contradiction_type", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column(
            "enqueued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["memory_id_a"], ["memory_entry.id"]),
        sa.ForeignKeyConstraint(["memory_id_b"], ["memory_entry.id"]),
    )
    op.create_index(
        "idx_judicial_human_queue_pending",
        "judicial_human_queue",
        ["enqueued_at"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )

    # Per 010: new tables default to append-only for jeli_app. Grant back only
    # the column-scoped UPDATEs the precedent system legitimately needs.
    op.execute("REVOKE UPDATE, DELETE ON judicial_precedent FROM jeli_app")
    op.execute("GRANT SELECT, INSERT ON judicial_precedent TO jeli_app")
    op.execute(
        "GRANT UPDATE (applied_count, confidence, last_applied_at) "
        "ON judicial_precedent TO jeli_app"
    )

    op.execute("REVOKE UPDATE, DELETE ON judicial_human_queue FROM jeli_app")
    op.execute("GRANT SELECT, INSERT ON judicial_human_queue TO jeli_app")
    op.execute(
        "GRANT UPDATE (resolved_at, resolution, resolved_by) "
        "ON judicial_human_queue TO jeli_app"
    )


def downgrade() -> None:
    op.drop_index("idx_judicial_human_queue_pending", table_name="judicial_human_queue")
    op.drop_table("judicial_human_queue")
    op.drop_table("judicial_precedent")
