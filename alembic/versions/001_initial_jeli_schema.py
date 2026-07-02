"""Initial Jeli schema: memory_entry, audit_log, contradiction tables.

Revision ID: 001_initial
Revises:
Create Date: 2026-05-18 17:30:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create initial Jeli tables."""

    # memory_entry — Core table with hash-chain integrity
    op.create_table(
        "memory_entry",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.func.gen_random_uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),  # SHA256
        sa.Column("embedding", postgresql.UUID(as_uuid=True), nullable=False),  # Will be vector(1536) with pgvector
        sa.Column("embedding_model", sa.String(255), nullable=False),  # 'openai/text-embedding-3-small'
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),  # 1536
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("trust_score", sa.Numeric(precision=3, scale=2), nullable=False),  # 0.3 to 1.0
        sa.Column("memory_type", sa.String(50), nullable=False),  # preference, identity, episodic, etc.
        sa.Column("prev_hash", sa.String(256), nullable=True),  # Previous record's hash (hash-chain)
        sa.Column("record_hash", sa.String(256), nullable=False),  # HMAC-SHA256(chain_key, canonical(...))
        sa.Column("valid_from", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amended_from", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("delta_embedding", postgresql.UUID(as_uuid=True), nullable=True),  # Will be vector(1536)
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),  # Discord user ID or agent name
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_agent", sa.String(100), nullable=True),  # hermes, claude, dispatch
        sa.Column("provenance_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["superseded_by"], ["memory_entry.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["amended_from"], ["memory_entry.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provenance_ref"], ["memory_entry.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("record_hash"),
    )

    # Indices for memory_entry
    op.create_index("idx_memory_valid_time", "memory_entry", ["valid_from", "valid_until"], postgresql_where=sa.text("valid_until IS NULL"))
    op.create_index("idx_memory_content_hash", "memory_entry", ["content_hash"])
    op.create_index("idx_memory_created_by", "memory_entry", ["created_by"])
    op.create_index("idx_memory_trust_score", "memory_entry", ["trust_score"])

    # memory_audit_log — Append-only immutable audit trail
    op.create_table(
        "memory_audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),  # created, amended, superseded, searched, redacted
        sa.Column("actor", sa.String(255), nullable=False),  # Discord user ID or agent name
        sa.Column("source_session", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["memory_id"], ["memory_entry.id"]),
    )

    # Indices for memory_audit_log
    op.create_index(
        "idx_audit_memory",
        "memory_audit_log",
        ["memory_id", sa.text("timestamp DESC")],
    )

    # memory_contradiction — Unresolved contradictions (flags for Judicial layer)
    op.create_table(
        "memory_contradiction",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.func.gen_random_uuid(), nullable=False),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conflicting_memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contradiction_type", sa.String(50), nullable=False),  # direct, temporal, trust_conflict
        sa.Column("severity", sa.String(20), nullable=False),  # low, medium, high
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved", sa.Boolean(), default=False, nullable=False),
        sa.Column("judicial_ruling_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["memory_id"], ["memory_entry.id"]),
        sa.ForeignKeyConstraint(["conflicting_memory_id"], ["memory_entry.id"]),
        sa.ForeignKeyConstraint(["judicial_ruling_id"], ["memory_entry.id"], ondelete="SET NULL"),
        sa.CheckConstraint("memory_id < conflicting_memory_id"),
    )

    # Indices for memory_contradiction
    op.create_index("idx_contradiction_unresolved", "memory_contradiction", ["memory_id"], postgresql_where=sa.text("resolved = FALSE"))


def downgrade() -> None:
    """Drop all Jeli tables."""
    op.drop_table("memory_contradiction")
    op.drop_table("memory_audit_log")
    op.drop_table("memory_entry")
