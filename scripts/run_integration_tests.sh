#!/usr/bin/env bash
set -euo pipefail

# Spin up a disposable pgvector Postgres, migrate it, run the live-DB
# integration suite against it, then tear it down. Run from the repo root with
# the project venv active (the integration tests import jeli_scoped_mcp).

COMPOSE_FILE="docker-compose.test.yml"

cleanup() {
  docker compose -f "$COMPOSE_FILE" down -v
}
trap cleanup EXIT

# Start test DB
docker compose -f "$COMPOSE_FILE" up -d

# Wait for healthy
echo "Waiting for DB..."
for _ in $(seq 1 30); do
  if docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U jeli_test >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# Run migrations against test DB (alembic needs DDL privileges; the container's
# POSTGRES_USER is a superuser of jeli_test, so app and admin URLs coincide).
export SCOPED_MCP_DB_URL="postgresql://jeli_test:jeli_test_password@localhost:5433/jeli_test"
export SCOPED_MCP_ADMIN_DB_URL="$SCOPED_MCP_DB_URL"
python -m alembic upgrade head

# Run integration tests (tests skip themselves unless JELI_TEST_DB_URL is set)
export JELI_TEST_DB_URL="$SCOPED_MCP_DB_URL"
python -m pytest tests/integration/ -v --tb=short --no-cov
