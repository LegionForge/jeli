#!/usr/bin/env bash
# Launch the Jeli scoped MCP server on stdio, loading secrets from files —
# values never appear in argv, logs, or the parent environment.
#
# Config (env, all optional):
#   JELI_SECRETS_DIR   dir with jeli_chain_key.txt / pg_jeli_app.txt  [~/.jeli-secrets]
#   JELI_DB_HOST/PORT/NAME/USER                    [127.0.0.1 / 5432 / jeli / jeli_app]
#   SCOPED_MCP_AGENT_ACTOR   principal identity for this server instance
#
# Register with Claude Code (example):
#   claude mcp add --scope user jeli \
#     -e SCOPED_MCP_AGENT_ACTOR=claude-code-<user> -e JELI_DB_PORT=5442 \
#     -- bash /path/to/scripts/run_mcp_stdio.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${JELI_SECRETS_DIR:-$HOME/.jeli-secrets}"

SCOPED_MCP_CHAIN_KEY="$(cat "$DIR/jeli_chain_key.txt")"
export SCOPED_MCP_CHAIN_KEY
DB_PW="$(cat "$DIR/pg_jeli_app.txt")"
export SCOPED_MCP_DB_URL="postgresql://${JELI_DB_USER:-jeli_app}:${DB_PW}@${JELI_DB_HOST:-127.0.0.1}:${JELI_DB_PORT:-5432}/${JELI_DB_NAME:-jeli}"
export SCOPED_MCP_TRANSPORT=stdio
export SCOPED_MCP_AGENT_ACTOR="${SCOPED_MCP_AGENT_ACTOR:-unknown-agent}"

PY="$HERE/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" -m jeli_scoped_mcp
