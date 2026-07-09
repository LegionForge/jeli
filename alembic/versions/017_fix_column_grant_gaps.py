"""017 — fix column-grant gaps found while auditing the 016 grant bug.

016 found and fixed one case of a real class of bug: a table's column-scoped
UPDATE grant (introduced in 010/013/014/015 to enforce append-only history)
listed fewer columns than the application's actual UPDATE statements
reference. Postgres checks column privilege syntactically against the SET
clause, not by whether the value changes, so any statement touching an
ungranted column fails closed against the real jeli_app role — silently,
since these code paths are rarely covered by integration tests that run
under the actual restricted role.

Auditing every column-scoped grant against its call sites found two more:

  memory_entry (010) granted (embedding, valid_until, superseded_by,
      amended_from), but `jeli reembed`'s UPDATE (cli.py) also sets
      embedding_model, embedding_dimensions, embedded_at when re-embedding
      after a model change. Verified live: `UPDATE memory_entry SET
      embedding_model = embedding_model ... WHERE false` fails with
      "permission denied" under jeli_app. `jeli reembed` — the documented
      re-embedding maintenance path (CLAUDE.md: "semantic search parity on
      re-embedding") — has never worked against the real role.

  entity_relation (015) granted (last_seen_at, evidence_count), but
      graph/store.py's record_relation() ON CONFLICT DO UPDATE also sets
      confidence (GREATEST of old/new). Verified live: same failure mode.

Revision ID: 017_fix_column_grant_gaps
Revises: 016_precedent_corroboration
Create Date: 2026-07-09
"""

from alembic import op

revision = "017_fix_column_grant_gaps"
down_revision = "016_precedent_corroboration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "GRANT UPDATE (embedding_model, embedding_dimensions, embedded_at) "
        "ON memory_entry TO jeli_app"
    )
    op.execute("GRANT UPDATE (confidence) ON entity_relation TO jeli_app")


def downgrade() -> None:
    op.execute(
        "REVOKE UPDATE (embedding_model, embedding_dimensions, embedded_at) "
        "ON memory_entry FROM jeli_app"
    )
    op.execute("REVOKE UPDATE (confidence) ON entity_relation FROM jeli_app")
