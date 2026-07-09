"""013 — Constitutional Layer: user-signed, inviolable read-time constraints.

The Constitutional layer is the inviolable tier of Jeli's three-branch model:
user-signed rules that no agent and no branch can override. Rules are enforced
at query time by the Read Gate (src/jeli_scoped_mcp/constitutional/gate.py),
which filters search results before any agent sees them.

Each rule is HMAC-signed with the chain key (rule_hash), so tampering with a
rule's type, parameters, or scope is detectable by `jeli constitutional verify`
— the same integrity guarantee memory_entry and memory_state_event carry.

Append-only like the memory chain: rules are never DELETEd. A rule is retired
by setting revoked_at (and active=FALSE), preserving the full history of what
constraints were ever in force. jeli_app gets column-scoped UPDATE on exactly
(active, revoked_at) for revocation and no DELETE at all.

Revision ID: 013_constitutional_layer
Revises: 012_real_fts
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "013_constitutional_layer"
down_revision = "012_real_fts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "constitutional_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column("rule_type", sa.Text(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("applies_to", sa.Text(), nullable=False, server_default="all"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rule_hash", sa.Text(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False, server_default="k1"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "rule_type IN ('exclude_memory_type','min_trust_floor','exclude_tag',"
            "'exclude_content_class','max_results','deny_write_memory_type',"
            "'max_trust_for_content_class')",
            name="ck_constitutional_rule_type",
        ),
    )
    op.create_index(
        "idx_constitutional_rules_active",
        "constitutional_rules",
        ["active"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # Append-only: revocation flips (active, revoked_at); content of a rule is
    # frozen after insert and rules are never deleted.
    op.execute("REVOKE UPDATE, DELETE ON constitutional_rules FROM jeli_app")
    op.execute("GRANT SELECT, INSERT ON constitutional_rules TO jeli_app")
    op.execute("GRANT UPDATE (active, revoked_at) ON constitutional_rules TO jeli_app")


def downgrade() -> None:
    op.drop_index("idx_constitutional_rules_active", table_name="constitutional_rules")
    op.drop_table("constitutional_rules")
