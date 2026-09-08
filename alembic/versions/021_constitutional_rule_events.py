"""021 — append-only constitutional lifecycle events.

Revocation changes constitutional authority, so it is represented as a signed
terminal fact rather than mutable flags on the rule body. Existing, internally
consistent revocations become immutable migration baselines. Contradictory old
flags stop the migration for explicit operator review instead of guessing.

Revision ID: 021_constitutional_rule_events
Revises: 020_constitutional_role_boundary
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "021_constitutional_rule_events"
down_revision = "020_constitutional_role_boundary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM constitutional_rules
                WHERE active = (revoked_at IS NOT NULL)
            ) THEN
                RAISE EXCEPTION
                    'inconsistent constitutional lifecycle flags require review';
            END IF;
        END $$
    """)
    op.create_table(
        "constitutional_rule_event",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("constitutional_rules.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_hash", sa.Text(), nullable=True),
        sa.Column("key_id", sa.Text(), nullable=True),
        sa.Column(
            "migration_baseline",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "event_type", name="uq_rule_terminal_event"),
        sa.CheckConstraint("event_type = 'revoked'", name="ck_rule_event_type"),
        sa.CheckConstraint(
            "(migration_baseline AND event_hash IS NULL AND key_id IS NULL) OR "
            "(NOT migration_baseline AND event_hash IS NOT NULL AND key_id IS NOT NULL)",
            name="ck_rule_event_auth_shape",
        ),
    )
    op.execute("""
        INSERT INTO constitutional_rule_event
            (rule_id, event_type, event_at, migration_baseline)
        SELECT id, 'revoked', revoked_at, TRUE
        FROM constitutional_rules
        WHERE active = FALSE AND revoked_at IS NOT NULL
    """)
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE ON constitutional_rule_event FROM jeli_app"
    )
    op.execute("GRANT SELECT ON constitutional_rule_event TO jeli_app")


def downgrade() -> None:
    # Dropping authenticated lifecycle history would silently reactivate rules.
    pass
