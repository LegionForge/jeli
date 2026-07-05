"""Alembic environment configuration for Jeli database migrations."""

from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import os
from pathlib import Path

# This is the Alembic Config object, which provides the values of the [alembic] section
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        # Logging configuration may have issues; skip it
        pass

# Migrations need DDL privileges (jeli_admin). Prefer SCOPED_MCP_ADMIN_DB_URL
# if set; fall back to SCOPED_MCP_DB_URL for environments where a single
# superuser account covers both roles.
db_url = (
    os.getenv("SCOPED_MCP_ADMIN_DB_URL")
    or os.getenv("SCOPED_MCP_DB_URL", "postgresql://jeli_app@127.0.0.1:5442/jeli")
)
config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to standard output)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to actual database)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
