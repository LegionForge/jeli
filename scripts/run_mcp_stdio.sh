#!/usr/bin/env bash
# Launch the Jeli scoped MCP server on stdio, loading secrets from files —
# values never appear in argv, logs, or the parent environment.
#
# Config (env, all optional):
#   JELI_SECRETS_DIR    dir with secret files                       [~/.jeli-secrets]
#   JELI_KEY_PROVIDER   "chain_key" (default) or "openbao"
#   JELI_DB_HOST/PORT/NAME/USER                    [127.0.0.1 / 5432 / jeli / jeli_app]
#   SCOPED_MCP_AGENT_ACTOR   principal identity for this server instance
#
#   chain_key provider reads, in JELI_SECRETS_DIR:
#     jeli_chain_key.txt   -> SCOPED_MCP_CHAIN_KEY
#
#   openbao provider reads, in JELI_SECRETS_DIR:
#     bao_token.txt        -> BAO_TOKEN (required)
#     openbao-cert.pem     -> BAO_CACERT (if present; else BAO_CACERT env, if set)
#   and honors, as plain (non-secret) env overrides:
#     SCOPED_MCP_KEY_REF   [secret/jeli-chain-key#value]
#     BAO_ADDR             [https://127.0.0.1:8200]
#
# Register with Claude Code (example):
#   claude mcp add --scope user jeli \
#     -e SCOPED_MCP_AGENT_ACTOR=claude-code-<user> -e JELI_DB_PORT=5442 \
#     -e JELI_KEY_PROVIDER=openbao \
#     -- bash /path/to/scripts/run_mcp_stdio.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${JELI_SECRETS_DIR:-$HOME/.jeli-secrets}"
PROVIDER="${JELI_KEY_PROVIDER:-chain_key}"

if [ "$PROVIDER" = "openbao" ]; then
    BAO_TOKEN="$(cat "$DIR/bao_token.txt")"
    export BAO_TOKEN
    export SCOPED_MCP_KEY_PROVIDER=openbao
    export SCOPED_MCP_KEY_REF="${SCOPED_MCP_KEY_REF:-secret/jeli-chain-key#value}"
    export BAO_ADDR="${BAO_ADDR:-https://127.0.0.1:8200}"
    if [ -z "${BAO_CACERT:-}" ] && [ -f "$DIR/openbao-cert.pem" ]; then
        export BAO_CACERT="$DIR/openbao-cert.pem"
    fi
else
    SCOPED_MCP_CHAIN_KEY="$(cat "$DIR/jeli_chain_key.txt")"
    export SCOPED_MCP_CHAIN_KEY
fi

DB_PW="$(cat "$DIR/pg_jeli_app.txt")"
export SCOPED_MCP_DB_URL="postgresql://${JELI_DB_USER:-jeli_app}:${DB_PW}@${JELI_DB_HOST:-127.0.0.1}:${JELI_DB_PORT:-5432}/${JELI_DB_NAME:-jeli}"
export SCOPED_MCP_TRANSPORT=stdio
export SCOPED_MCP_AGENT_ACTOR="${SCOPED_MCP_AGENT_ACTOR:-unknown-agent}"

PY="$HERE/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" -m jeli_scoped_mcp
