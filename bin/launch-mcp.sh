#!/bin/bash
# Launches the Jeli MCP server with the project .env loaded.
# Used as the MCP command entry in Claude Code settings — keeps secrets
# out of settings.json by relying on pydantic-settings .env discovery.
#
# Runs alembic upgrade head before starting so new migrations apply
# automatically on every restart. Alembic is idempotent — already-applied
# migrations are skipped in ~20ms.
set -e
cd /Volumes/MAC_MINI_1TB/LegionForge-jeli
.venv/bin/alembic upgrade head
exec .venv/bin/python -m jeli_scoped_mcp
