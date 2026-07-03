"""memory_state_event: hash-chained authority for temporal state changes.

Closes the THREAT-MODEL "temporal fields" gap: valid_until / superseded_by /
amended_from are set after write, so they cannot live inside the write-time
record hash — an attacker with UPDATE rights could resurrect a retracted
memory or hide a live one without breaking any hash. From 006 on, every
state change is ALSO recorded as a record in this table's own HMAC chain;
the mutable columns become a queryable cache whose authority is the event
chain. `jeli verify` cross-checks the cache against the chain.

Also creates the `jeli_user` role pattern (column-level grants) — see
scripts/setup_db_roles.sql: user-tier operations may set temporal columns
but remain structurally unable to modify content.

Revision ID: 006_state_events
Revises: 005_chain_seq
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "006_state_events"
down_revision = "005_chain_seq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS state_event_chain_seq")
    op.create_table(
        "memory_state_event",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column(
            "chain_seq",
            sa.BigInteger(),
            server_default=sa.text("nextval('state_event_chain_seq')"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(30), nullable=False),  # superseded | invalidated
        sa.Column("target_memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("successor_memory_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prev_hash", sa.String(256), nullable=True),
        sa.Column("record_hash", sa.String(256), nullable=False),
        sa.Column("key_id", sa.String(64), nullable=False, server_default="k1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["target_memory_id"], ["memory_entry.id"]),
        sa.ForeignKeyConstraint(["successor_memory_id"], ["memory_entry.id"]),
        sa.UniqueConstraint("chain_seq", name="uq_state_event_chain_seq"),
        sa.UniqueConstraint("record_hash", name="uq_state_event_record_hash"),
        sa.CheckConstraint("event_type IN ('superseded','invalidated')"),
    )
    op.create_index("idx_state_event_target", "memory_state_event", ["target_memory_id"])


def downgrade() -> None:
    op.drop_table("memory_state_event")
    op.execute("DROP SEQUENCE IF EXISTS state_event_chain_seq")
