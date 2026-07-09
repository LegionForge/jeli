"""015 — Entity graph: named entities + memory links + entity relations.

Jeli stores memories as text + embeddings but has no notion of the *entities*
they mention or how those entities relate. This adds a lightweight personal
knowledge graph:

  entity              — canonical named things (people, projects, orgs, tech…),
                        with aliases for alternate spellings.
  memory_entity_link  — which memory mentions/describes/is-about which entity.
  entity_relation     — entity ↔ entity edges (works_on, part_of…), reinforced
                        over time (evidence_count / last_seen_at) as the same
                        relation is observed again.

Extraction is rule-based and best-effort on the write path (see
graph/extractor.py) — it never blocks or fails a memory write. All three tables
are append-mostly for jeli_app, matching the store's discipline: SELECT+INSERT
everywhere, column-scoped UPDATE only where a row legitimately evolves (entity
aliases/metadata, relation evidence), and no DELETE.

Revision ID: 015_entity_graph
Revises: 014_judicial_precedent
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "015_entity_graph"
down_revision = "014_judicial_precedent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "entity_type", name="uq_entity_name_type"),
        sa.CheckConstraint(
            "entity_type IN ('person','project','organization','concept',"
            "'location','technology')",
            name="ck_entity_type",
        ),
    )

    op.create_table(
        "memory_entity_link",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.Text(), nullable=False, server_default="mentions"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["memory_id"], ["memory_entry.id"]),
        sa.ForeignKeyConstraint(["entity_id"], ["entity.id"]),
        sa.UniqueConstraint("memory_id", "entity_id", "relation", name="uq_mel_link"),
    )

    op.create_table(
        "entity_relation",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("predicate", sa.Text(), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["subject_id"], ["entity.id"]),
        sa.ForeignKeyConstraint(["object_id"], ["entity.id"]),
        sa.UniqueConstraint("subject_id", "predicate", "object_id", name="uq_entity_relation"),
    )

    op.execute(
        "CREATE INDEX idx_entity_name ON entity USING gin(to_tsvector('english', name))"
    )
    op.execute("CREATE INDEX idx_entity_type ON entity(entity_type)")
    op.execute("CREATE INDEX idx_mel_memory ON memory_entity_link(memory_id)")
    op.execute("CREATE INDEX idx_mel_entity ON memory_entity_link(entity_id)")
    op.execute("CREATE INDEX idx_er_subject ON entity_relation(subject_id)")
    op.execute("CREATE INDEX idx_er_object ON entity_relation(object_id)")

    # Append-mostly per 010: new tables default to no UPDATE/DELETE for jeli_app.
    # Grant back only the columns that legitimately evolve.
    op.execute("REVOKE UPDATE, DELETE ON entity FROM jeli_app")
    op.execute("GRANT SELECT, INSERT ON entity TO jeli_app")
    op.execute("GRANT UPDATE (aliases, metadata) ON entity TO jeli_app")

    op.execute("REVOKE UPDATE, DELETE ON memory_entity_link FROM jeli_app")
    op.execute("GRANT SELECT, INSERT ON memory_entity_link TO jeli_app")

    op.execute("REVOKE UPDATE, DELETE ON entity_relation FROM jeli_app")
    op.execute("GRANT SELECT, INSERT ON entity_relation TO jeli_app")
    op.execute("GRANT UPDATE (last_seen_at, evidence_count) ON entity_relation TO jeli_app")


def downgrade() -> None:
    op.drop_table("entity_relation")
    op.drop_table("memory_entity_link")
    op.drop_table("entity")
