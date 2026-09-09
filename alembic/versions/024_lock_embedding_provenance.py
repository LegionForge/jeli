"""024 — make original embedding provenance immutable to the app role.

Re-embedding now updates the separately labeled current-index provenance.
Remove the runtime role's ability to alter the original model, dimensions,
and timestamp because those fields are canonical HMAC inputs.

Revision ID: 024_lock_embedding_provenance
Revises: 023_index_embedding_provenance
Create Date: 2026-09-09
"""

from alembic import op

revision = "024_lock_embedding_provenance"
down_revision = "023_index_embedding_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "REVOKE UPDATE (embedding_model, embedding_dimensions, embedded_at) "
        "ON memory_entry FROM jeli_app"
    )
    op.execute(
        "GRANT UPDATE (index_embedding_model, index_embedding_dimensions, "
        "index_embedded_at) ON memory_entry TO jeli_app"
    )


def downgrade() -> None:
    # Restoring writes to authenticated provenance would reopen the defect.
    pass
