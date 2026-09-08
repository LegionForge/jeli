"""019 — enforce least-privilege attributes on the Jeli runtime role.

The official PostgreSQL container creates POSTGRES_USER as a cluster
superuser. Older documented compose configuration used ``jeli_app`` there,
silently defeating every table/column grant. Demote any existing runtime role
and remove migration-administrator membership. This migration intentionally
fails unless it runs under an identity allowed to administer roles.

Revision ID: 019_harden_app_role
Revises: 018_entity_relation_evidence
Create Date: 2026-09-07
"""

from alembic import op

revision = "019_harden_app_role"
down_revision = "018_entity_relation_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'jeli_app') THEN
                EXECUTE 'ALTER ROLE jeli_app NOSUPERUSER NOCREATEDB '
                        'NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS';
                IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'jeli_admin') THEN
                    EXECUTE 'REVOKE jeli_admin FROM jeli_app';
                END IF;
            END IF;
        END $$
    """)


def downgrade() -> None:
    # Security hardening is intentionally irreversible.
    pass
