"""016 — Judicial precedent corroboration: Sybil/single-source gating.

GH #44 (MINJA cross-turn threat capture) and the 2026-07-07 red-team
assessment both flagged the same gap: judicial_precedent.applied_count and
confidence climb on *agreement count* alone, with no notion of *who* is
agreeing. N repeat deliberations from one actor currently earn a precedent
identical authority to N deliberations from N independent actors — exactly
the manufactured-corroboration channel docs/trust-doctrine.md warns about
("ten outlets owned by one entity are one source wearing ten hats").

This adds judicial_precedent_corroboration: one row per (precedent, distinct
source_agent) that has ever produced an agreeing deliberation for that
pattern. PrecedentStore now only grows confidence on a *new* source's first
agreement; repeat agreement from an already-seen source still counts toward
applied_count (observability) but no longer inflates confidence. This directly
narrows the MINJA-style bridging-record precedent-poisoning lever without
needing new retrieval infrastructure (tracked as non-blocking future work
in GH #44).

Also fixes a live grant bug found while making this change: migration 014
granted jeli_app UPDATE only on (applied_count, confidence, last_applied_at),
but judicial_precedent.record()'s UPDATE statement's SET clause always
references resolution and winner_rule too (Postgres checks column privilege
syntactically, not by whether the value actually changes) — so every call to
record() has been failing closed with "permission denied for table
judicial_precedent" against the real jeli_app role since 014 shipped. Verified
live: 0 rows in judicial_precedent in production, consistent with this path
never having been exercised end-to-end. Grants are extended here.

Revision ID: 016_precedent_corroboration
Revises: 015_entity_graph
Create Date: 2026-07-09
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "016_precedent_corroboration"
down_revision = "015_entity_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "judicial_precedent_corroboration",
        sa.Column("precedent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("precedent_id", "source_key"),
        sa.ForeignKeyConstraint(
            ["precedent_id"], ["judicial_precedent.id"], ondelete="CASCADE"
        ),
    )

    # Append-only: dedup is via ON CONFLICT DO NOTHING on the PK, never UPDATE/DELETE.
    op.execute("REVOKE UPDATE, DELETE ON judicial_precedent_corroboration FROM jeli_app")
    op.execute("GRANT SELECT, INSERT ON judicial_precedent_corroboration TO jeli_app")

    # Fix 014: resolution/winner_rule are always in record()'s UPDATE SET
    # clause (even on agree/dissent branches that leave them unchanged), so
    # jeli_app needs column UPDATE privilege on them, not just the counters.
    op.execute(
        "GRANT UPDATE (resolution, winner_rule) ON judicial_precedent TO jeli_app"
    )


def downgrade() -> None:
    op.execute("REVOKE UPDATE (resolution, winner_rule) ON judicial_precedent FROM jeli_app")
    op.drop_table("judicial_precedent_corroboration")
