"""Privilege-layer append-only enforcement (GH #11, migration 010).

Verifies that the jeli_app role is structurally unable to rewrite or delete
hash-chained history, while queue tables stay mutable. Runs as the admin user
from JELI_TEST_DB_URL and drops to jeli_app via SET ROLE.
"""

import os

import pytest

from jeli_scoped_mcp.database.pool import AsyncPostgresPool

DB_URL = os.getenv("JELI_TEST_DB_URL")

pytestmark = pytest.mark.skipif(
    not DB_URL, reason="JELI_TEST_DB_URL not set (live integration only)"
)


@pytest.fixture
async def db():
    pool = AsyncPostgresPool(DB_URL, min_size=1, max_size=2)
    await pool.connect()
    yield pool
    await pool.close()


async def _as_jeli_app(db, sql: str, *args):
    """Run one statement as jeli_app inside a rolled-back transaction."""
    assert db.pool is not None
    async with db.pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            try:
                await conn.execute("SET LOCAL ROLE jeli_app")
            except Exception:
                pytest.skip("test user cannot SET ROLE jeli_app")
            return await conn.execute(sql, *args)
        finally:
            await tx.rollback()


@pytest.mark.asyncio
async def test_app_cannot_update_hashed_columns(db):
    with pytest.raises(Exception, match="permission denied"):
        await _as_jeli_app(
            db, "UPDATE memory_entry SET content = 'tampered' WHERE false"
        )
    with pytest.raises(Exception, match="permission denied"):
        await _as_jeli_app(
            db, "UPDATE memory_entry SET trust_score = 1.0 WHERE false"
        )


@pytest.mark.asyncio
async def test_app_cannot_delete_chained_rows(db):
    for table in ("memory_entry", "memory_audit_log", "memory_state_event"):
        with pytest.raises(Exception, match="permission denied"):
            await _as_jeli_app(db, f"DELETE FROM {table} WHERE false")


@pytest.mark.asyncio
async def test_app_column_scoped_temporal_updates_allowed(db):
    # Temporal cache columns are settable (authority is the state-event
    # chain; jeli verify cross-checks) — hashed fields stay frozen above.
    await _as_jeli_app(
        db, "UPDATE memory_entry SET valid_until = now() WHERE false"
    )
    await _as_jeli_app(
        db, "UPDATE memory_entry SET embedding = NULL WHERE false"
    )


@pytest.mark.asyncio
async def test_app_queue_tables_stay_mutable(db):
    await _as_jeli_app(
        db, "UPDATE memory_inbox SET status = 'pending' WHERE false"
    )
    await _as_jeli_app(db, "DELETE FROM memory_inbox WHERE false")
    await _as_jeli_app(
        db, "UPDATE memory_conflict_queue SET status = 'pending' WHERE false"
    )
