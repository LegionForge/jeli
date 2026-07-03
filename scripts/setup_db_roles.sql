-- Jeli append-only enforcement — at the privilege layer, not in code.
--
-- The MCP server connects as jeli_app, which structurally CANNOT update or
-- delete memory or audit rows. Even a fully compromised Jeli process cannot
-- rewrite history. Run once as a superuser/owner after `alembic upgrade head`:
--
--   psql -d jeli -f scripts/setup_db_roles.sql
--
-- Then point SCOPED_MCP_DB_URL at jeli_app. Migrations keep running as the
-- owning (admin) role, never as jeli_app.
--
-- Note: temporal invalidation (valid_until / superseded_by) is designed as an
-- UPDATE and is therefore NOT grantable to jeli_app; in Phase 1 those fields
-- are set by the admin role only. See docs/THREAT-MODEL.md §"Temporal fields".

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'jeli_app') THEN
        CREATE ROLE jeli_app LOGIN;
    END IF;
END
$$;

-- \password jeli_app   -- set interactively; never store the password in SQL

REVOKE ALL ON memory_entry, memory_audit_log, memory_contradiction FROM jeli_app;

GRANT SELECT, INSERT ON memory_entry TO jeli_app;
GRANT SELECT, INSERT ON memory_audit_log TO jeli_app;
GRANT SELECT, INSERT ON memory_contradiction TO jeli_app;

-- audit log id is BIGSERIAL; INSERT needs the sequence
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO jeli_app;

-- explicitly no UPDATE, no DELETE, no TRUNCATE, anywhere.

-- ── user tier ────────────────────────────────────────────────────────────────
-- jeli_user: JP's own CLI operations (jeli revise / invalidate). May retire
-- memories via COLUMN-level grants — structurally unable to modify content.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'jeli_user') THEN
        CREATE ROLE jeli_user LOGIN;
    END IF;
END
$$;

REVOKE ALL ON memory_entry, memory_audit_log, memory_contradiction, memory_state_event FROM jeli_user;
GRANT SELECT, INSERT ON memory_entry TO jeli_user;
GRANT SELECT, INSERT ON memory_audit_log TO jeli_user;
GRANT SELECT, INSERT ON memory_state_event TO jeli_user;
GRANT SELECT ON memory_contradiction TO jeli_user;
GRANT UPDATE (valid_until, superseded_by, amended_from) ON memory_entry TO jeli_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO jeli_user;
