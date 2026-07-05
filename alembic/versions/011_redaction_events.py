"""011 — 'redacted' joins the state-event vocabulary

Redaction is redesigned as a chained memory_state_event (GH #13): the old
implementation rewrote memory_entry.content in place, but content is inside
the canonical record hash, so the first real redaction made verify_chain
report the record as tampered. From 011 on, redaction appends a 'redacted'
event to the state chain; content is masked at read time and the row is never
rewritten.

Revision ID: 011_redaction_events
Revises: 010_app_role_append_only
Create Date: 2026-07-05
"""

from alembic import op

revision = "011_redaction_events"
down_revision = "010_app_role_append_only"
branch_labels = None
depends_on = None


_DROP_EVENT_TYPE_CHECKS = """
DO $$
DECLARE c record;
BEGIN
    -- 006 created the CHECK inline without a name; drop whatever the server
    -- called it rather than guessing.
    FOR c IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'memory_state_event'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%event_type%'
    LOOP
        EXECUTE format('ALTER TABLE memory_state_event DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$
"""


def upgrade() -> None:
    op.execute(_DROP_EVENT_TYPE_CHECKS)
    op.execute(
        "ALTER TABLE memory_state_event ADD CONSTRAINT "
        "memory_state_event_event_type_check "
        "CHECK (event_type IN ('superseded','invalidated','redacted'))"
    )


def downgrade() -> None:
    op.execute(_DROP_EVENT_TYPE_CHECKS)
    op.execute(
        "ALTER TABLE memory_state_event ADD CONSTRAINT "
        "memory_state_event_event_type_check "
        "CHECK (event_type IN ('superseded','invalidated'))"
    )
