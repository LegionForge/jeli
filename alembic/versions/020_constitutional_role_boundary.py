"""020 — separate constitutional mutation authority from the runtime role.

The constitutional CLI is user-tier; the MCP runtime is agent-tier.  The
runtime role therefore receives read access only.  Older container guidance
could also leave jeli_app owning the table, which would bypass every GRANT, so
ownership is transferred to the migration administrator where necessary.

Revision ID: 020_constitutional_role_boundary
Revises: 019_harden_app_role
Create Date: 2026-09-08
"""

from alembic import op

revision = "020_constitutional_role_boundary"
down_revision = "019_harden_app_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        DECLARE table_owner text;
        BEGIN
            SELECT pg_get_userbyid(c.relowner) INTO table_owner
            FROM pg_class c
            WHERE c.oid = 'constitutional_rules'::regclass;

            IF table_owner = 'jeli_app' THEN
                IF NOT EXISTS (
                    SELECT FROM pg_roles WHERE rolname = 'jeli_admin'
                ) THEN
                    RAISE EXCEPTION
                        'jeli_admin is required to take constitutional_rules ownership';
                END IF;
                ALTER TABLE constitutional_rules OWNER TO jeli_admin;
            END IF;
        END $$
    """)
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE ON constitutional_rules FROM jeli_app"
    )
    op.execute("GRANT SELECT ON constitutional_rules TO jeli_app")


def downgrade() -> None:
    # Never restore agent-tier authority over user constitutional rules.
    pass
