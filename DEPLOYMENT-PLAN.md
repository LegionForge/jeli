# Jeli Deployment Plan: Local Sovereign Memory

**Version:** 1.0-draft  
**Date:** 2026-06-06  
**Target:** Mac Mini M4 (16GB, 1TB SSD) + LF2B stack  
**Related:** TECHNICAL-SPECIFICATION.md, CURATION-ALGORITHM.md

---

## Deployment Architecture

```
┌─────────────────────────────────────────────────────────┐
│              Mac Mini M4 (JP's Machine)                  │
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │  launchd (Mac Native Process Management)           │ │
│  │  ├─ jeli-postgres (PostgreSQL 15, port 5442)      │ │
│  │  ├─ jeli-curation (Python, hourly cron + API)     │ │
│  │  ├─ jeli-mcp (MCP server, stdio/TCP)              │ │
│  │  └─ jeli-redis (Redis local, L0 cache)            │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │  Data Storage (SSD)                                │ │
│  │  ├─ /Volumes/MAC_MINI_1TB/jeli-data/              │ │
│  │  │  ├─ postgresql/        (L1/L2 tables)          │ │
│  │  │  ├─ redis-dump.rdb     (L0 cache)              │ │
│  │  │  ├─ archive/           (L3 cold storage)       │ │
│  │  │  └─ audit-log/         (immutable hash-chain)  │ │
│  │  └─ Backups (daily snapshots)                     │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
└─────────────────────────────────────────────────────────┘
         ↓ (via MCP stdio)
    Claude Code / Hermes / Future Agents
```

---

## Component Deployment

### 1. PostgreSQL (L1 + L2 Storage)

**Why Local PostgreSQL?**
- Sovereignty: data never leaves machine
- Performance: <50ms queries on SSD
- Simplicity: no cloud management overhead
- Integration: OB1 already uses this stack

**Installation & Setup:**

```bash
# If not already installed:
brew install postgresql@15

# Start service (launchd, persistent)
brew services start postgresql@15

# Verify
pg_isready -h 127.0.0.1 -p 5442

# Create jeli database
createdb -h 127.0.0.1 -p 5442 jeli

# Run migrations (Alembic)
cd /Volumes/MAC_MINI_1TB/LegionForge-jeli
alembic upgrade head
```

**Configuration (`~/.config/jeli/postgres.conf`):**
```ini
[postgres]
host = 127.0.0.1
port = 5442
database = jeli
user = jp

# Performance tuning for 16GB Mac Mini
shared_buffers = 4GB
effective_cache_size = 12GB
work_mem = 100MB
random_page_cost = 1.1  # SSD-optimized
```

**Backup Strategy:**

```bash
# Daily backup (cron job)
# Add to ~/Library/LaunchAgents/com.legionforge.jeli-backup.plist

0 2 * * * /Volumes/MAC_MINI_1TB/LegionForge-jeli/bin/backup.sh

# Backup script keeps:
# - Last 30 daily backups (1 month)
# - Last 12 weekly backups
# - Last 12 monthly backups
# Total: ~50GB with compression
```

---

### 2. Redis (L0 Hot Cache)

**Why Local Redis?**
- Sub-millisecond latency (in-memory)
- Built-in TTL (auto-eviction)
- Persistence (RDB dumps)
- No cloud dependency

**Installation & Setup:**

```bash
# Install
brew install redis

# Start service (launchd)
brew services start redis

# Verify
redis-cli ping  # Should respond PONG

# Check memory usage
redis-cli info memory
```

**Configuration (`/usr/local/etc/redis.conf`):**
```ini
# Port (default 6379, Jeli uses this)
port 6379

# Persistence (RDB dumps)
save 3600 1         # Save if 1 change in 1 hour
save 300 10         # Save if 10 changes in 5 minutes

# Memory policy (LRU eviction)
maxmemory 500mb     # L0 cache size limit
maxmemory-policy allkeys-lru  # Evict least recently used

# Rewrite AOF periodically (optional)
appendonly no       # Disable for speed (RDB is enough)
```

**Startup (launchd plist):**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.legionforge.jeli-redis</string>
    <key>Program</key>
    <string>/usr/local/bin/redis-server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/redis-server</string>
        <string>/usr/local/etc/redis.conf</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Volumes/MAC_MINI_1TB/jeli-data/redis.log</string>
    <key>StandardErrorPath</key>
    <string>/Volumes/MAC_MINI_1TB/jeli-data/redis-error.log</string>
</dict>
</plist>
```

Save as `~/Library/LaunchAgents/com.legionforge.jeli-redis.plist` and run:
```bash
launchctl load ~/Library/LaunchAgents/com.legionforge.jeli-redis.plist
```

---

### 3. Python Curation Engine

**Why Python?**
- Alembic (migrations)
- SQLAlchemy (ORM)
- scikit-learn (ML scoring)
- Easy to test + debug

**Setup:**

```bash
cd /Volumes/MAC_MINI_1TB/LegionForge-jeli

# Create venv
python3.11 -m venv venv
source venv/bin/activate

# Install deps
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Verify
python -m jeli.core.curation --test
```

**Curation Job (Hourly):**

```bash
# Create launchd plist for hourly curation job
# ~/Library/LaunchAgents/com.legionforge.jeli-curation.plist

<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.legionforge.jeli-curation</string>
    <key>Program</key>
    <string>/Volumes/MAC_MINI_1TB/LegionForge-jeli/bin/run-curation.sh</string>
    <key>StartInterval</key>
    <integer>3600</integer>  <!-- Every hour -->
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Volumes/MAC_MINI_1TB/jeli-data/curation.log</string>
    <key>StandardErrorPath</key>
    <string>/Volumes/MAC_MINI_1TB/jeli-data/curation-error.log</string>
</dict>
</plist>
```

**Curation Script (`bin/run-curation.sh`):**
```bash
#!/bin/bash
set -e

export JELI_HOME=/Volumes/MAC_MINI_1TB/LegionForge-jeli
cd $JELI_HOME

# Activate venv
source venv/bin/activate

# Run curation job
python -m jeli.jobs.evict_and_promote \
  --postgres-uri postgresql://jp:@127.0.0.1:5442/jeli \
  --redis-uri redis://127.0.0.1:6379 \
  --log-file /Volumes/MAC_MINI_1TB/jeli-data/curation.log

echo "Curation job completed at $(date)" >> /Volumes/MAC_MINI_1TB/jeli-data/curation.log
```

---

### 4. MCP Server (Agent Interface)

**Why MCP?**
- Standard protocol (Anthropic, other tools support it)
- Scoped access (agents can't do arbitrary SQL)
- Local stdio (no network overhead)
- Security (validates all writes)

**Setup:**

```bash
# Implementation in Node.js/Deno (easier than Python for MCP)
cd /Volumes/MAC_MINI_1TB/LegionForge-jeli

npm install @modelcontextprotocol/sdk

# Create server file: src/mcp-server.ts
# Exposes:
# - jeli.recall(query, scope)
# - jeli.remember(memory, scope)
# - jeli.get(id)
# - jeli.feedback(id, interaction)
```

**Startup (launchd plist):**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.legionforge.jeli-mcp</string>
    <key>Program</key>
    <string>/usr/local/bin/node</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/node</string>
        <string>/Volumes/MAC_MINI_1TB/LegionForge-jeli/dist/mcp-server.js</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Volumes/MAC_MINI_1TB/jeli-data/mcp.log</string>
    <key>StandardErrorPath</key>
    <string>/Volumes/MAC_MINI_1TB/jeli-data/mcp-error.log</string>
</dict>
</plist>
```

**Integration with Claude Code:**

In Claude Code settings (`~/.config/Claude/settings.json`):
```json
{
  "mcpServers": {
    "jeli": {
      "command": "node",
      "args": ["/Volumes/MAC_MINI_1TB/LegionForge-jeli/dist/mcp-server.js"]
    }
  }
}
```

---

## Health Monitoring

### Dashboard Metrics

**Daily Health Check Script:**
```bash
#!/bin/bash
# bin/health-check.sh

echo "=== Jeli Health Check ===" $(date)

# PostgreSQL
pg_isready -h 127.0.0.1 -p 5442
  && echo "✓ PostgreSQL healthy" \
  || echo "✗ PostgreSQL DOWN"

# Redis
redis-cli ping
  && echo "✓ Redis healthy" \
  || echo "✗ Redis DOWN"

# Database size
psql -h 127.0.0.1 -p 5442 -d jeli -c "
  SELECT pg_size_pretty(pg_database_size('jeli')) as db_size;
"

# Fact count by layer
psql -h 127.0.0.1 -p 5442 -d jeli -c "
  SELECT 
    'L0 (hot)' as layer, COUNT(*) as count FROM memories_l0
  UNION ALL
  SELECT 'L1 (primary)', COUNT(*) FROM memories_l1_primary
  UNION ALL
  SELECT 'L2 (warm)', COUNT(*) FROM memories_l2_warm
  UNION ALL
  SELECT 'L3 (cold)', COUNT(*) FROM memories_l3_cold;
"

# Curation job status
tail -5 /Volumes/MAC_MINI_1TB/jeli-data/curation.log

# Backup status
ls -lt /Volumes/MAC_MINI_1TB/jeli-data/backups | head -5
```

**Run daily:**
```bash
# Add to cron
0 8 * * * /Volumes/MAC_MINI_1TB/LegionForge-jeli/bin/health-check.sh >> /Volumes/MAC_MINI_1TB/jeli-data/health.log
```

### Alerting

**Conditions to monitor:**

```python
# jeli/monitoring/alerts.py

ALERT_CONDITIONS = {
    "postgres_down": "pg_isready fails",
    "redis_down": "redis-cli ping fails",
    "db_size_over_50gb": "disk space critical",
    "curation_failed": "job exited with error",
    "hash_chain_break": "audit log validation fails",
    "high_contradiction_rate": "> 5% facts contradicted",
    "backup_failed": "no backup in last 24 hours",
}

# If alert triggers:
# 1. Log to /Volumes/MAC_MINI_1TB/jeli-data/alerts.log
# 2. Write to Obsidian vault (automated incident note)
# 3. Optional: send email to jp_cruz@yahoo.com
```

---

## Startup Sequence

**On Mac reboot:**

```bash
1. launchctl loads all jeli plist files (automatic)
2. PostgreSQL starts (port 5442)
3. Redis starts (port 6379)
4. MCP server starts (stdio/TCP)
5. First curation job runs (scheduled for +1 hour)

# Verify all running:
launchctl list | grep com.legionforge.jeli
ps aux | grep -E "postgres|redis|node" | grep -v grep

# Check logs:
tail -f /Volumes/MAC_MINI_1TB/jeli-data/*.log
```

---

## OB1 Integration (Optional)

If JP decides to integrate Jeli with OB1:

**Architecture:**
```
OB1 (Ingestion, Retrieval)
  ↓
Jeli MCP Layer (Security, Tiering, Governance)
  ↓
PostgreSQL (Shared or Federated)
```

**Implementation:**
- OB1 writes memories via Jeli MCP tools (scoped access)
- Jeli handles curation, hash-chain, contradiction detection
- PostgreSQL stores both OB1 and Jeli metadata

**No changes to OB1 required** — Jeli is an optional overlay.

---

## Operational Runbooks

### Daily Startup Check

```bash
# Run health check
/Volumes/MAC_MINI_1TB/LegionForge-jeli/bin/health-check.sh

# Expected output:
# ✓ PostgreSQL healthy
# ✓ Redis healthy
# db_size: 2.3 GB
# L1 count: 1,500
# L2 count: 8,000
# ...
```

### Emergency: Restore from Backup

```bash
# If corruption detected:

# 1. Stop services
launchctl stop com.legionforge.jeli-postgres
launchctl stop com.legionforge.jeli-curation

# 2. Find latest backup
ls -lt /Volumes/MAC_MINI_1TB/jeli-data/backups/ | head -1

# 3. Restore
pg_restore -d jeli /Volumes/MAC_MINI_1TB/jeli-data/backups/jeli-backup-2026-06-06.sql.gz

# 4. Restart
launchctl start com.legionforge.jeli-postgres
launchctl start com.legionforge.jeli-curation

# 5. Verify audit log integrity
jeli verify
```

### Manual Curation Intervention

```bash
# If automated curation fails:

# 1. Activate venv
source /Volumes/MAC_MINI_1TB/LegionForge-jeli/venv/bin/activate

# 2. Run manual job
python -m jeli.jobs.evict_and_promote --verbose --dry-run

# 3. Review output
# If looks good, run without --dry-run

# 4. Verify
psql -h 127.0.0.1 -p 5442 -d jeli -c "SELECT AVG(significance) FROM memories_l1_primary;"
```

---

## Disaster Recovery Plan

**Data Loss Scenarios:**

| Scenario | Recovery Time | Data Loss |
|----------|---------------|-----------|
| PostgreSQL crash | 10 min | None (restore from backup) |
| Redis cache lost | 5 min | None (L0 cache regenerates on next recall) |
| Disk full | 30 min | None (archive old L2/L3 to external) |
| Corruption detected | 2 hours | None (restore from backup, reverify) |
| Complete hardware failure | 1 day | None (restore to new Mac Mini) |

**Backup Locations:**
- Primary: `/Volumes/MAC_MINI_1TB/jeli-data/backups/` (daily)
- Secondary: iCloud Drive (weekly encrypted backup)
- Tertiary: GitHub (encrypted archive, offline backup)

---

## Performance Baselines (Target)

```
L0 (Redis) recall:     <1ms
L1 (PostgreSQL) recall: 5-50ms (depending on query complexity)
L2 escalation:         100-500ms
L3 archive fetch:      1-10s

Fact insert latency:   <100ms (write + hash-chain)
Contradiction check:   <1s (semantic similarity)
Eviction job runtime:  5-10 min (hourly, ~1,000 facts processed)

Storage footprint:
  L0 (Redis):        <500MB
  L1 (PostgreSQL):   10-100MB
  L2 (PostgreSQL):   100MB-1GB
  L3 (Files):        Unlimited
  Backups (30 daily): ~50GB
```

---

## Success Criteria (Deployment)

- [ ] All services start automatically on Mac reboot
- [ ] PostgreSQL healthy, backups run daily
- [ ] Redis caching working, L0 latency <1ms
- [ ] MCP server accessible to Claude Code
- [ ] Curation job runs hourly, no errors
- [ ] Health checks pass (all green)
- [ ] Can restore from backup in <10 min
- [ ] Monitoring alerts working
- [ ] Operational runbooks tested

---

## Next Steps

1. **Phase 1 PoC (2 weeks):** Deploy L0+L1, test basic recall/remember
2. **Phase 2 (2 weeks):** Curation engine, eviction job, health monitoring
3. **Phase 3 (2 weeks):** L2/L3 tiering, archive operations
4. **Phase 4 (1 week):** OB1 integration (optional)
5. **Production (ongoing):** Monitor, backup, update

