"""010 — restore append-only privileges for jeli_app

Migration 008 granted blanket SELECT, INSERT, UPDATE, DELETE on all tables to
jeli_app (both as default privileges and retroactively), silently undoing the
append-only enforcement in scripts/setup_db_roles.sql. A compromised MCP
process could rewrite or delete memory and audit rows — the exact attack the
role split exists to make structurally impossible (GH #11).

This migration revokes the broad grants and re-grants per table, per the rule:
hash-chained history is append-only; operational queues are mutable.

  memory_entry — SELECT, INSERT + column UPDATE on exactly:
      embedding                  archival nulls the vector to drop it from the
                                 HNSW index; not part of the canonical hash
      valid_until, superseded_by,
      amended_from               temporal cache columns; their authority is the
                                 memory_state_event chain (006) — flips without
                                 a chained event are detected by `jeli verify`
    content, trust_score, metadata, memory_type, prev_hash, record_hash and
    every other hashed field are structurally frozen after insert.
  memory_audit_log, memory_contradiction, memory_archive — SELECT, INSERT only.
  memory_state_event — SELECT, INSERT only (chained; never mutated).
  memory_inbox — mutable queue (worker claim/status + retention cleanup).
  memory_conflict_queue, daemon_runs — mutable status (no DELETE).

Full user-tier separation (CLI state ops connecting as jeli_user instead of
jeli_app) is follow-up work; the grants here already make content history
immutable for every role except the migration owner.

Revision ID: 010_app_role_append_only
Revises: 009_content_class
Create Date: 2026-07-05
"""

from alembic import op

revision = "010_app_role_append_only"
down_revision = "009_content_class"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Undo 008's blanket write grants.
    op.execute("REVOKE UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM jeli_app")

    # Future objects created by jeli_admin default to append-only for jeli_app;
    # anything needing queue semantics gets an explicit grant in its migration.
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE jeli_admin IN SCHEMA public
            REVOKE UPDATE, DELETE ON TABLES FROM jeli_app
    """)

    # Queue tables: legitimately mutable by the app-tier daemons.
    op.execute("GRANT UPDATE, DELETE ON memory_inbox TO jeli_app")
    op.execute("GRANT UPDATE ON memory_conflict_queue TO jeli_app")
    op.execute("GRANT UPDATE ON daemon_runs TO jeli_app")

    # Column-scoped exceptions on memory_entry (see module docstring).
    op.execute(
        "GRANT UPDATE (embedding, valid_until, superseded_by, amended_from) "
        "ON memory_entry TO jeli_app"
    )


def downgrade() -> None:
    # Restore 008's (over-broad) behavior.
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE jeli_admin IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO jeli_app
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO jeli_app")
