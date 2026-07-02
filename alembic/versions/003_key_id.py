"""Add key_id: which chain key signed each record.

key_id lives INSIDE the canonical hashed form (see core/hash_chain.py), so
it must exist before any real data does — retrofitting it would change
every record hash. Enables per-record key rotation and the OpenBAO transit
path (vault key versions).

Revision ID: 003_key_id
Revises: 002_embedding_jsonb
"""

import sqlalchemy as sa
from alembic import op

revision = "003_key_id"
down_revision = "002_embedding_jsonb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_entry",
        sa.Column("key_id", sa.String(64), nullable=False, server_default="k1"),
    )
    op.create_index("idx_memory_key_id", "memory_entry", ["key_id"])


def downgrade() -> None:
    op.drop_index("idx_memory_key_id", table_name="memory_entry")
    op.drop_column("memory_entry", "key_id")
