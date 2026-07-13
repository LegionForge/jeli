#!/bin/bash
# launchd entrypoint for Jeli background daemons. Waits for the services the
# daemon needs (PostgreSQL, Ollama, OpenBAO when configured) before exec'ing
# the blocking CLI runner. Installed to ~/Library/Application Support by
# scripts/install-launchd.sh; launchd cannot read entrypoints or env files on
# external volumes, which is why the installed copy and env file live on the
# internal disk.
#
# Exit codes:
#   0 — deliberate no-run (missing config); with KeepAlive.SuccessfulExit=false
#       launchd will NOT respawn, so a broken install can't crash-loop
#   1 — dependency timeout; launchd may retry after ThrottleInterval
#   2 — usage error
set -u

MODE="${1:-start}"
ENV_FILE="${JELI_ENV_FILE:-$HOME/Library/Application Support/LegionForge/jeli.env}"
MAX_WAIT="${JELI_DEP_MAX_WAIT:-300}"

export PATH="/opt/homebrew/bin:/opt/homebrew/opt/postgresql@17/bin:/usr/local/bin:/usr/bin:/bin"
export LC_ALL=C

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"; }

case "$MODE" in
    start|insights|maintenance) ;;
    *) log "ERROR: unsupported mode: $MODE"; exit 2 ;;
esac

# Pre-flight failures exit 0 on purpose: respawning cannot fix a missing file,
# and KeepAlive would otherwise retry forever. Re-run install-launchd.sh.
if [[ ! -r "$ENV_FILE" ]]; then
    log "FATAL: cannot read env file $ENV_FILE — run scripts/install-launchd.sh (exit 0, no respawn)"
    exit 0
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

PROJECT_DIR="${JELI_PROJECT_DIR:-}"
if [[ -z "$PROJECT_DIR" || ! -d "$PROJECT_DIR" ]]; then
    log "FATAL: JELI_PROJECT_DIR unset or missing ($PROJECT_DIR) — set it in the plist or env file (exit 0, no respawn)"
    exit 0
fi
PYTHON="$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    log "FATAL: $PYTHON not executable (exit 0, no respawn)"
    exit 0
fi

# Endpoints come from the same env the application reads, not hardcoded copies.
DB_URL="${SCOPED_MCP_DB_URL:-postgresql://jeli_app@127.0.0.1:5442/jeli}"
hostport="${DB_URL#*://}"     # strip scheme
hostport="${hostport##*@}"    # strip credentials
hostport="${hostport%%/*}"    # strip /dbname
DB_HOST="${hostport%%:*}"
DB_PORT="${hostport##*:}"
[[ "$DB_PORT" == "$DB_HOST" ]] && DB_PORT=5432
OLLAMA_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
KEY_PROVIDER="${SCOPED_MCP_KEY_PROVIDER:-env}"

db_check() {
    if command -v pg_isready > /dev/null 2>&1; then
        pg_isready -h "$DB_HOST" -p "$DB_PORT" -q
    else
        (exec 3<> "/dev/tcp/$DB_HOST/$DB_PORT") 2> /dev/null
    fi
}

bao_check() {
    # Only the openbao key provider needs the vault at startup.
    [[ "$KEY_PROVIDER" != "openbao" ]] && return 0
    bao status -format=json 2> /dev/null \
        | grep -Eq '"sealed"[[:space:]]*:[[:space:]]*false'
}

wait_for_dependencies() {
    # SECONDS is bash's wall clock — includes check time, not just the sleeps.
    local deadline=$((SECONDS + MAX_WAIT)) next_report=0 db=false ollama=false bao=false
    while true; do
        db=false ollama=false bao=false
        db_check && db=true
        curl -fsS --max-time 3 "$OLLAMA_URL/api/tags" > /dev/null 2>&1 && ollama=true
        bao_check && bao=true
        if $db && $ollama && $bao; then
            log "Dependencies ready after ${SECONDS}s."
            return 0
        fi
        if ((SECONDS >= next_report)); then
            log "waiting on dependencies (db=$db ollama=$ollama bao[$KEY_PROVIDER]=$bao, ${SECONDS}s elapsed)"
            next_report=$((SECONDS + 30))
        fi
        if ((SECONDS >= deadline)); then
            log "ERROR: dependencies unavailable after ${SECONDS}s (db=$db ollama=$ollama bao[$KEY_PROVIDER]=$bao)"
            return 1
        fi
        sleep 5
    done
}

cd "$PROJECT_DIR" || exit 0
wait_for_dependencies || exit 1
log "Starting jeli daemon $MODE."
exec "$PYTHON" -m jeli_scoped_mcp.cli daemon "$MODE"
