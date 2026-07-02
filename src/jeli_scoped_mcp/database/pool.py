"""Async PostgreSQL connection pooling via asyncpg."""

from contextlib import asynccontextmanager
from typing import Any

import asyncpg


class AsyncPostgresPool:
    """Connection pool manager for async Postgres operations."""

    def __init__(self, db_url: str, min_size: int = 5, max_size: int = 20):
        """Initialize connection pool configuration."""
        self.db_url = db_url
        self.min_size = min_size
        self.max_size = max_size
        self.pool: asyncpg.Pool | None = None

    async def connect(self):
        """Create the connection pool."""
        self.pool = await asyncpg.create_pool(
            self.db_url,
            min_size=self.min_size,
            max_size=self.max_size,
            command_timeout=30,
        )

    async def close(self):
        """Close the connection pool."""
        if self.pool:
            await self.pool.close()

    async def execute(self, query: str, *args) -> Any:
        """Execute a query and return the result."""
        if not self.pool:
            raise RuntimeError("Connection pool not initialized")
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetchrow(self, query: str, *args) -> asyncpg.Record | None:
        """Fetch a single row."""
        if not self.pool:
            raise RuntimeError("Connection pool not initialized")
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchall(self, query: str, *args) -> list[asyncpg.Record]:
        """Fetch all rows."""
        if not self.pool:
            raise RuntimeError("Connection pool not initialized")
        async with self.pool.acquire() as conn:
            rows: list[asyncpg.Record] = await conn.fetch(query, *args)
            return rows

    async def fetchval(self, query: str, *args) -> Any:
        """Fetch a single value."""
        if not self.pool:
            raise RuntimeError("Connection pool not initialized")
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    @asynccontextmanager
    async def locked_transaction(self, lock_key: int):
        """One transaction holding a pg advisory lock — serializes chain writes.

        The prev-hash read and the insert must be atomic or two concurrent
        writers fork the chain (same prev_hash twice) and verify_chain flags
        legitimate data as tampered.
        """
        if not self.pool:
            raise RuntimeError("Connection pool not initialized")
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)
                yield conn

    async def health_check(self) -> bool:
        """Check if pool is healthy."""
        try:
            if not self.pool:
                return False
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False
