"""009 — content_class + source_metadata columns for memory_inbox

Adds two columns to memory_inbox:
  content_class   — content category ('general', 'security-doc', 'code-sample',
                    'external-untrusted'); drives two-axis trust logic in the
                    injection defense layer.
  source_metadata — preserves caller-supplied metadata (source_path, chunk_index,
                    etc.) through the inbox pipeline so workers can pass it on.

Revision ID: 009_content_class
Revises: 008_default_privileges
Create Date: 2026-07-04
"""

import sqlalchemy as sa

from alembic import op

revision = "009_content_class"
down_revision = "008_default_privileges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_inbox",
        sa.Column("content_class", sa.Text(), nullable=False, server_default="general"),
    )
    op.add_column(
        "memory_inbox",
        sa.Column("source_metadata", sa.dialects.postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("memory_inbox", "source_metadata")
    op.drop_column("memory_inbox", "content_class")
