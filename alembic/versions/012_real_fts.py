"""012 — real full-text search for memory_entry.content

The 'fts' search mode was a leading-wildcard ILIKE substring scan: no
tokenization, no ranking, and unindexable, degrading linearly with table size
(GH #18). This adds an expression GIN index so mode=fts can use
websearch_to_tsquery + ts_rank.

An expression index (not a generated column) on purpose: archival relies on
memory_archive staying column-identical to memory_entry
(INSERT INTO memory_archive SELECT * FROM memory_entry), and a new stored
column would break that parity.

Revision ID: 012_real_fts
Revises: 011_redaction_events
Create Date: 2026-07-05
"""

from alembic import op

revision = "012_real_fts"
down_revision = "011_redaction_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_entry_content_fts "
        "ON memory_entry USING GIN (to_tsvector('english', content))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_memory_entry_content_fts")
