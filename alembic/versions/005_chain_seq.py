"""chain_seq: authoritative, monotonic chain order.

created_at cannot order the chain: Postgres now() is transaction-start
time, and chain writes serialize on an advisory lock AFTER their
transactions begin — concurrent writers therefore tie on created_at and
the verify walk (and the find-latest query) see the wrong order. A
sequence claimed inside the locked section is strictly increasing in true
chain order.

Existing rows are backfilled in (created_at, id) order — safe because all
pre-005 data was written sequentially (single writer / import).

Revision ID: 005_chain_seq
Revises: 004_pgvector_1024
"""

import sqlalchemy as sa
from alembic import op

revision = "005_chain_seq"
down_revision = "004_pgvector_1024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS memory_chain_seq")
    op.add_column("memory_entry", sa.Column("chain_seq", sa.BigInteger(), nullable=True))
    # backfill in write order (pre-005 data was sequential; created_at unique)
    op.execute("""
        UPDATE memory_entry m SET chain_seq = sub.rn
        FROM (
            SELECT id, row_number() OVER (ORDER BY created_at ASC, id ASC) AS rn
            FROM memory_entry
        ) sub
        WHERE m.id = sub.id
        """)
    op.execute(
        "SELECT setval('memory_chain_seq', COALESCE((SELECT max(chain_seq) FROM memory_entry), 1))"
    )
    op.execute(
        "ALTER TABLE memory_entry ALTER COLUMN chain_seq SET DEFAULT nextval('memory_chain_seq')"
    )
    op.alter_column("memory_entry", "chain_seq", nullable=False)
    op.create_unique_constraint("uq_memory_chain_seq", "memory_entry", ["chain_seq"])


def downgrade() -> None:
    op.drop_constraint("uq_memory_chain_seq", "memory_entry")
    op.drop_column("memory_entry", "chain_seq")
    op.execute("DROP SEQUENCE IF EXISTS memory_chain_seq")
