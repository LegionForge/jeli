"""022 — label memory and state-event canonical HMAC formats.

Existing rows and current writers use canonical v1. Explicit version labels
let runtime dual verification distinguish that legacy evidence from future v2
records without silently re-signing it. ``memory_archive`` receives its column
immediately after ``memory_entry`` to preserve their positional copy contract.

Revision ID: 022_canonical_versions
Revises: 021_constitutional_rule_events
Create Date: 2026-09-09
"""

import sqlalchemy as sa

from alembic import op

revision = "022_canonical_versions"
down_revision = "021_constitutional_rule_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_entry",
        sa.Column(
            "canonical_version",
            sa.SmallInteger(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "memory_archive",
        sa.Column(
            "canonical_version",
            sa.SmallInteger(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "memory_state_event",
        sa.Column(
            "canonical_version",
            sa.SmallInteger(),
            nullable=False,
            server_default="1",
        ),
    )

    op.create_check_constraint(
        "ck_memory_entry_canonical_version",
        "memory_entry",
        "canonical_version IN (1, 2)",
    )
    op.create_check_constraint(
        "ck_memory_archive_canonical_version",
        "memory_archive",
        "canonical_version IN (1, 2)",
    )
    op.create_check_constraint(
        "ck_memory_state_event_canonical_version",
        "memory_state_event",
        "canonical_version IN (1, 2)",
    )


def downgrade() -> None:
    # Removing these labels after v2 rows exist would make authenticated
    # evidence ambiguous. Cryptographic format versioning is irreversible.
    pass
