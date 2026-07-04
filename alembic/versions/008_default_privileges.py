"""008 — default privileges for jeli_app

Ensures jeli_admin-created objects are automatically accessible to jeli_app.
Also retroactively grants on all objects created in earlier migrations.

Revision ID: 008_default_privileges
Revises: 007_memory_inbox
Create Date: 2026-07-04
"""

from alembic import op

revision = "008_default_privileges"
down_revision = "007_memory_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Future objects: any table/sequence/function that jeli_admin creates in
    # public schema will automatically be accessible to jeli_app.
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE jeli_admin IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO jeli_app
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE jeli_admin IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO jeli_app
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE jeli_admin IN SCHEMA public
            GRANT EXECUTE ON FUNCTIONS TO jeli_app
    """)

    # Retroactive: grant on all objects already in place (migrations 001-007).
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO jeli_app"
    )
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO jeli_app"
    )
    op.execute(
        "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO jeli_app"
    )


def downgrade() -> None:
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE jeli_admin IN SCHEMA public
            REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM jeli_app
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE jeli_admin IN SCHEMA public
            REVOKE USAGE, SELECT ON SEQUENCES FROM jeli_app
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE jeli_admin IN SCHEMA public
            REVOKE EXECUTE ON FUNCTIONS FROM jeli_app
    """)
