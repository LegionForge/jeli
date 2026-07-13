# Running the Jeli Daemons Under launchd (macOS)

`jeli daemon start` is a blocking foreground runner (InboxWorker +
ConflictResolver). Without a service manager it simply stops when its shell
does, and queued inbox records sit unprocessed. On macOS the supported
deployment is launchd, installed by:

```bash
scripts/install-launchd.sh                 # install + bootstrap all three jobs
scripts/install-launchd.sh daemons         # just the persistent runner
scripts/install-launchd.sh --no-bootstrap  # render/copy files only
```

Three jobs are installed from the templates in `launchd/`:

| Label | What | Schedule |
|---|---|---|
| `com.legionforge.jeli-daemons` | InboxWorker + ConflictResolver (`daemon start`) | persistent, respawn on crash |
| `com.legionforge.jeli-insights` | `daemon insights` one-shot | daily 02:30 |
| `com.legionforge.jeli-maintenance` | `daemon maintenance` one-shot | daily 03:30 |

Logs: `~/Library/Logs/LegionForge/jeli-*.log`.

## Why the indirection (macOS constraints, learned the hard way)

- **launchd cannot execute entrypoint scripts or read env files that live on
  an external volume** (TCC denies with `Operation not permitted` / exit 126).
  The installer therefore copies the launcher and the repo `.env` to
  `~/Library/Application Support/LegionForge/` (env copy is mode 600). Both
  copies are derived artifacts: edit the repo versions and re-run the
  installer. Executing the repo's external-volume `.venv/bin/python` is fine —
  only the entrypoint and env-file reads are blocked.
- **launchd background jobs are denied LAN unicast by macOS Local Network
  privacy** ("no route to host" for `10.x` addresses that work fine in a
  terminal). Loopback services (PostgreSQL, Ollama) are unaffected, but a
  vault/OpenBAO endpoint must be reachable through an exempt route — a
  Tailscale/utun address works; the machine's LAN subnet does not.

## Launcher behavior

`scripts/jeli-daemon-launcher.sh` waits (default 300s, `JELI_DEP_MAX_WAIT`)
for PostgreSQL, Ollama, and — only when `SCOPED_MCP_KEY_PROVIDER=openbao` —
an unsealed, authenticated OpenBAO before exec'ing the daemon. Endpoints are
derived from the same env file the application reads (`SCOPED_MCP_DB_URL`,
`OLLAMA_BASE_URL`, `BAO_ADDR`), not hardcoded.

Exit semantics are tuned for launchd's `KeepAlive.SuccessfulExit=false`:
unfixable configuration problems (missing env file, missing venv) log a FATAL
line and exit 0 so launchd does **not** respawn into a crash loop; a
dependency timeout exits 1 so launchd retries after `ThrottleInterval`.

While waiting it logs an unready snapshot every 30s
(`db=… ollama=… bao[provider]=…`) — read the out-log to see exactly which
dependency is holding startup.

## Secrets

The env copy in Application Support contains the same secrets as the repo
`.env` (mode 600, internal disk). Nothing is ever written to the plists or
logs. After rotating any credential, update the repo `.env` and re-run the
installer to re-sync the copy.
