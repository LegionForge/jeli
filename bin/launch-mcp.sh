#!/bin/bash
# Launches the Jeli MCP server with the project .env loaded.
# Used as the MCP command entry in Claude Code settings — keeps secrets
# out of settings.json by relying on pydantic-settings .env discovery.
#
# Before starting, applies any pending Alembic migrations with two guards:
#
#   1. Git dirty check — refuses to auto-migrate if alembic/versions/ has
#      uncommitted changes. Protects against in-flight edits (interrupted save,
#      power loss, cat on keyboard) producing a syntactically-valid-but-wrong
#      migration that runs before you notice. Workflow: edit → commit → restart.
#
#   2. Pre-migration backup — if there are pending migrations, pg_dump the DB
#      to backups/ before applying them. One restore point per migration batch.
#      Skip with JELI_SKIP_BACKUP=1 if pg_dump is unavailable or you're in CI.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

BACKUP_DIR="$REPO_ROOT/backups"
ALEMBIC=".venv/bin/alembic"
PYTHON=".venv/bin/python"

# Alembic reads connection URLs from the process environment (unlike the
# application, it does not load pydantic's .env file). Export the same local
# configuration before migration checks and server startup.
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
fi

# ── 1. Git dirty check ────────────────────────────────────────────────────────
# Uncommitted changes to migration files = potentially half-edited SQL.
# Refuse to auto-migrate; require a clean commit first.
if git rev-parse --git-dir > /dev/null 2>&1; then
    if ! git diff --quiet HEAD -- alembic/versions/ 2>/dev/null; then
        echo "ERROR: alembic/versions/ has uncommitted changes." >&2
        echo "       Finish editing, then: git add alembic/versions/ && git commit" >&2
        echo "       Restart the server once the migration is committed." >&2
        exit 1
    fi
fi

# ── 2. Pre-migration backup ───────────────────────────────────────────────────
# Check whether any migrations are pending before doing the expensive pg_dump.
# `upgrade head --sql` always emits a script preamble, even at head, so compare
# Alembic's current revision to its declared head instead.
if [ "${JELI_SKIP_BACKUP:-0}" != "1" ] && command -v pg_dump > /dev/null 2>&1; then
    CURRENT_REV=$("$ALEMBIC" current 2>/dev/null | awk 'NR == 1 {print $1}' || true)
    HEAD_REV=$("$ALEMBIC" heads 2>/dev/null | awk 'NR == 1 {print $1}' || true)
    if [ -n "$HEAD_REV" ] && [ "$CURRENT_REV" != "$HEAD_REV" ]; then
        mkdir -p "$BACKUP_DIR"
        BACKUP_FILE="$BACKUP_DIR/pre-migrate-$(date +%Y%m%d-%H%M%S).sql"
        # Pull DB URL from the env the same way pydantic-settings does.
        # Prefer admin URL for backup too (has read rights; app URL works either way)
        DB_URL=$(grep -E '^SCOPED_MCP_ADMIN_DB_URL=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
        DB_URL=${DB_URL:-$(grep -E '^SCOPED_MCP_DB_URL=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' || true)}
        if [ -n "$DB_URL" ]; then
            pg_dump "$DB_URL" > "$BACKUP_FILE" \
                && echo "jeli: pre-migration backup → $BACKUP_FILE" >&2
        else
            echo "jeli: SCOPED_MCP_DB_URL not found in .env — skipping backup" >&2
        fi
    fi
fi

# ── 3. Apply migrations ───────────────────────────────────────────────────────
"$ALEMBIC" upgrade head

# ── 4. Start server ───────────────────────────────────────────────────────────
exec "$PYTHON" -m jeli_scoped_mcp
