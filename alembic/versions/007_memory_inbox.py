"""memory_inbox: Bouncer staging table, daemon_runs audit, memory_archive cold store,
memory_conflict_queue for distributed conflict resolution, and pg_notify trigger.

Revision ID: 007_memory_inbox
Revises: 006_state_events
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "007_memory_inbox"
down_revision = "006_state_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_inbox",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_agent", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("caller_trust", sa.Numeric(3, 2), nullable=False),
        sa.Column("caller_type", sa.Text(), nullable=False),
        # classifier outputs
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("worker_id", sa.Text(), nullable=True),  # set when claimed; identifies instance
        sa.Column("importance", sa.Numeric(3, 2), nullable=True),
        sa.Column("urgency", sa.Text(), nullable=True),
        sa.Column("durability", sa.Text(), nullable=True),
        sa.Column("encoding", sa.Text(), nullable=False, server_default="raw"),
        # enrichment
        sa.Column("suggested_type", sa.Text(), nullable=True),
        sa.Column("suggested_trust", sa.Numeric(3, 2), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("keywords", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "entities", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        # dedup
        sa.Column("near_duplicate_of", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("duplicate_distance", sa.Numeric(6, 4), nullable=True),
        sa.Column("merge_strategy", sa.Text(), nullable=True),
        # review
        sa.Column("requires_review", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("review_reason", sa.Text(), nullable=True),
        # audit
        sa.Column("classifier_version", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "enrichment_log",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending','processing','approved','merged','held','rejected')",
            name="ck_inbox_status",
        ),
        sa.CheckConstraint(
            "urgency IS NULL OR urgency IN ('low','medium','high','critical')",
            name="ck_inbox_urgency",
        ),
        sa.CheckConstraint(
            "durability IS NULL OR durability IN ('transient','session','durable','permanent')",
            name="ck_inbox_durability",
        ),
        sa.CheckConstraint(
            "encoding IN ('raw','summary','keywords','hybrid')",
            name="ck_inbox_encoding",
        ),
        sa.CheckConstraint(
            "merge_strategy IS NULL OR merge_strategy IN ('replace','append','skip')",
            name="ck_inbox_merge_strategy",
        ),
        sa.ForeignKeyConstraint(["near_duplicate_of"], ["memory_entry.id"]),
        sa.ForeignKeyConstraint(["promoted_to"], ["memory_entry.id"]),
    )
    op.create_index("idx_inbox_status_submitted", "memory_inbox", ["status", "submitted_at"])
    op.create_index("idx_inbox_content_hash", "memory_inbox", ["content_hash"])
    op.create_index("idx_inbox_near_duplicate", "memory_inbox", ["near_duplicate_of"])

    # Conflict resolution work queue — safe for N concurrent consumers via SKIP LOCKED.
    # pg_notify triggers the INSERT; workers drain via SELECT ... FOR UPDATE SKIP LOCKED.
    op.create_table(
        "memory_conflict_queue",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "enqueued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flags_found", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["memory_id"], ["memory_entry.id"]),
        sa.CheckConstraint(
            "status IN ('pending','processing','done','failed')",
            name="ck_conflict_queue_status",
        ),
    )
    op.create_index(
        "idx_conflict_queue_status", "memory_conflict_queue", ["status", "enqueued_at"]
    )
    op.create_index(
        "idx_conflict_queue_memory_id", "memory_conflict_queue", ["memory_id"]
    )

    op.create_table(
        "daemon_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column("daemon_name", sa.Text(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("instance_index", sa.Integer(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("items_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('running','completed','failed')", name="ck_daemon_runs_status"
        ),
    )
    op.create_index("idx_daemon_runs_name_started", "daemon_runs", ["daemon_name", "started_at"])

    # Cold storage — same shape as memory_entry, no vector index.
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_archive
        AS SELECT * FROM memory_entry WHERE false
    """)
    op.execute("ALTER TABLE memory_archive ADD PRIMARY KEY (id)")

    # pg_notify trigger enqueues a conflict-check row on every new memory_entry write.
    # Workers drain memory_conflict_queue via FOR UPDATE SKIP LOCKED — safe for N instances.
    op.execute("""
        CREATE OR REPLACE FUNCTION enqueue_conflict_check()
        RETURNS trigger AS $$
        BEGIN
          INSERT INTO memory_conflict_queue (memory_id) VALUES (NEW.id);
          PERFORM pg_notify('jeli_memory_written', NEW.id::text);
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS trg_enqueue_conflict_check ON memory_entry;
        CREATE TRIGGER trg_enqueue_conflict_check
          AFTER INSERT ON memory_entry
          FOR EACH ROW EXECUTE FUNCTION enqueue_conflict_check()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_enqueue_conflict_check ON memory_entry")
    op.execute("DROP FUNCTION IF EXISTS enqueue_conflict_check()")
    op.execute("DROP TABLE IF EXISTS memory_archive")
    op.drop_table("daemon_runs")
    op.drop_table("memory_conflict_queue")
    op.drop_table("memory_inbox")
