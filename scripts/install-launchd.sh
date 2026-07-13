#!/bin/bash
# Idempotent installer for Jeli's launchd background jobs.
#
# macOS launchd cannot read entrypoint scripts or env files on external
# volumes, so this copies the launcher and the repository .env to the internal
# disk, renders the plist templates with real paths, and (re)bootstraps the
# jobs. Re-run it after editing the launcher, the plists, or .env — the .env
# copy in Application Support is a derived artifact, never edited directly.
#
#   scripts/install-launchd.sh                 # install + bootstrap all jobs
#   scripts/install-launchd.sh daemons         # only com.legionforge.jeli-daemons
#   scripts/install-launchd.sh --no-bootstrap  # install files only
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_SUPPORT="$HOME/Library/Application Support/LegionForge"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/LegionForge"

BOOTSTRAP=1
JOBS=()
for arg in "$@"; do
    case "$arg" in
        --no-bootstrap) BOOTSTRAP=0 ;;
        daemons|insights|maintenance) JOBS+=("$arg") ;;
        *) echo "usage: $0 [--no-bootstrap] [daemons|insights|maintenance ...]" >&2; exit 2 ;;
    esac
done
[[ ${#JOBS[@]} -eq 0 ]] && JOBS=(daemons insights maintenance)

mkdir -p "$APP_SUPPORT" "$LAUNCH_AGENTS" "$LOG_DIR"

install -m 755 "$REPO_ROOT/scripts/jeli-daemon-launcher.sh" \
    "$APP_SUPPORT/jeli-daemon-launcher.sh"
echo "installed: $APP_SUPPORT/jeli-daemon-launcher.sh"

if [[ -f "$REPO_ROOT/.env" ]]; then
    # umask keeps the copy mode-600 for its entire existence, not just after a
    # later chmod. Secrets never touch stdout.
    (umask 077 && cp "$REPO_ROOT/.env" "$APP_SUPPORT/jeli.env")
    chmod 600 "$APP_SUPPORT/jeli.env"
    echo "synced: $APP_SUPPORT/jeli.env (mode 600, derived from repo .env)"
else
    echo "WARNING: $REPO_ROOT/.env not found — jobs will no-op until it exists" >&2
fi

for job in "${JOBS[@]}"; do
    label="com.legionforge.jeli-$job"
    template="$REPO_ROOT/launchd/$label.plist.template"
    target="$LAUNCH_AGENTS/$label.plist"
    sed -e "s|@HOME@|$HOME|g" -e "s|@PROJECT_DIR@|$REPO_ROOT|g" \
        "$template" > "$target"
    plutil -lint "$target" > /dev/null
    if [[ $BOOTSTRAP -eq 1 ]]; then
        # bootout completes asynchronously; bootstrapping before the old job is
        # fully unloaded fails with "Bootstrap failed: 5: Input/output error".
        if launchctl print "gui/$(id -u)/$label" > /dev/null 2>&1; then
            launchctl bootout "gui/$(id -u)/$label" 2> /dev/null || true
            for _ in $(seq 1 20); do
                launchctl print "gui/$(id -u)/$label" > /dev/null 2>&1 || break
                sleep 0.5
            done
        fi
        launchctl bootstrap "gui/$(id -u)" "$target"
        echo "bootstrapped: $label"
    else
        echo "rendered: $target (not bootstrapped)"
    fi
done

echo
echo "REMINDER: after rotating any credential in the repo .env, re-run this"
echo "script — the Application Support copy does not update itself."
