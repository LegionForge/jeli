# Hermes Docker Setup (Quick Start)

## Prerequisites
- Docker and Docker Compose installed
- Discord Bot Token (create at https://discord.com/developers/applications)
- Your Discord User ID (type `@YourName` in Discord to get it)
- Anthropic API key (separate from Claude Code; create at https://console.anthropic.com)

## Setup (5 minutes)

### 1. Create environment file
```bash
cp .env.hermes.template .env.hermes
```

### 2. Fill in values
Edit `.env.hermes`:
- `DISCORD_TOKEN`: Bot token from Discord Dev Portal
- `DISCORD_ALLOWED_USERS`: Your Discord user ID (from `@mention`)
- `ANTHROPIC_API_KEY`: API key from Anthropic console
- `POSTGRES_PASSWORD`: Generate: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`

### 3. Create workspace directory
```bash
mkdir -p hermes-workspace
chmod 700 hermes-workspace
```

### 4. Start containers
```bash
docker-compose -f docker-compose.hermes.yml up -d
```

### 5. Check status
```bash
docker-compose -f docker-compose.hermes.yml ps
docker logs hermes-sandbox --follow
```

## Testing Tonight

### Health Check
```bash
docker-compose -f docker-compose.hermes.yml exec hermes curl http://localhost:8000/health
```

### Verify Isolation
```bash
# Should be able to read workspace only
docker-compose -f docker-compose.hermes.yml exec hermes ls -la /workspace

# Should NOT have shell access
docker-compose -f docker-compose.hermes.yml exec hermes /bin/bash
# (will fail — expected)
```

### Discord Test
1. Invite bot to your Discord server
2. Type in channel: `@Hermes status`
3. Check logs: `docker logs hermes-sandbox --follow`

### Database Connection (Future)
```bash
# From host
psql -h 127.0.0.1 -p 5433 -U jeli_app -d jeli
# Password: from .env.hermes POSTGRES_PASSWORD
```

## Cleanup

### Stop containers
```bash
docker-compose -f docker-compose.hermes.yml down
```

### Remove data (reset)
```bash
docker volume rm jeli-db hermes-home
```

## Security Notes

✅ **What's Locked Down:**
- Hermes can only write to `/workspace` (HERMES_WRITE_SAFE_ROOT)
- No shell access to host
- No access to `.env` or credentials outside container
- Network isolated to `jeli-net`
- Capabilities dropped (NET_BIND_SERVICE only)
- Read-only filesystem except /tmp, /run

⚠️ **Still Missing (Phase 2):**
- No Scoped MCP (Hermes can call any tool)
- No agent identity binding
- Discord allowlist enforced locally, not in container

## Troubleshooting

### Hermes exits immediately
```bash
docker logs hermes-sandbox
# Check DISCORD_TOKEN and ANTHROPIC_API_KEY are valid
```

### Can't connect to Discord
```bash
# Verify bot token in Discord Dev Portal
# Verify bot has permissions: Send Messages, Read Message History
# Verify bot is invited to your server
```

### Workspace files appear as root
```bash
# Fix permissions
sudo chown -R $(id -u):$(id -g) hermes-workspace
```

## Next: Phase 2 Integration

When ready (after Scoped MCP implementation):
```bash
# Add to docker-compose.hermes.yml:
SCOPED_MCP_API_KEY: from .env.hermes
SCOPED_MCP_DB_URL: postgresql://jeli_app:password@jeli-postgres:5432/jeli
```

Then Hermes will capture memories to Jeli with full auditability.
