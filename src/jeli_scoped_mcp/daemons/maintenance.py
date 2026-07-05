"""MaintenanceDaemon — trust decay, archival, inbox cleanup."""

import logging
from datetime import UTC, datetime, timedelta

from ..database.pool import AsyncPostgresPool
from ..tools.memory_tools import MemoryTools

logger = logging.getLogger(__name__)


class MaintenanceDaemon:
    DECAY_BATCH_SIZE = 100
    ARCHIVE_AFTER_DAYS = 90
    INBOX_CLEANUP_AFTER_DAYS = 30

    def __init__(self, db: AsyncPostgresPool, memory_tools: MemoryTools):
        self.db = db
        self.memory_tools = memory_tools

    async def run_once(self) -> dict:
        results: dict = {}
        results["trust_decay"] = await self._apply_trust_decay()
        results["archival"] = await self._archive_expired()
        results["inbox_cleanup"] = await self._cleanup_old_inbox()
        return results

    async def _apply_trust_decay(self) -> dict:
        """Trust decay is disabled: trust_score is part of the canonical record
        hash, so mutating it in place makes every decayed record fail
        verify_chain — indistinguishable from tampering (GH #19). Migration 010
        also revokes UPDATE on the column. Decay will return as a read-time
        computation (effective_trust from age at query time), never a stored
        mutation.
        """
        return {"decayed": 0, "disabled": "stored decay breaks the hash chain (GH #19)"}

    async def _archive_expired(self) -> dict:
        """Move expired memories older than ARCHIVE_AFTER_DAYS to memory_archive."""
        cutoff = datetime.now(UTC) - timedelta(days=self.ARCHIVE_AFTER_DAYS)
        rows = await self.db.fetchall(
            """
            SELECT id FROM memory_entry
            WHERE valid_until IS NOT NULL
              AND valid_until < $1
            LIMIT 500
            """,
            cutoff,
        )
        archived = 0
        for row in rows:
            try:
                if not self.db.pool:
                    break
                async with self.db.pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            """
                            INSERT INTO memory_archive
                            SELECT * FROM memory_entry WHERE id = $1
                            ON CONFLICT (id) DO NOTHING
                            """,
                            row["id"],
                        )
                        # Null out the embedding so it falls out of the HNSW index.
                        await conn.execute(
                            "UPDATE memory_entry SET embedding = NULL WHERE id = $1",
                            row["id"],
                        )
                archived += 1
            except Exception:
                logger.warning("archive: failed for %s", row["id"], exc_info=True)
        return {"archived": archived}

    async def _cleanup_old_inbox(self) -> dict:
        """Remove resolved inbox rows older than INBOX_CLEANUP_AFTER_DAYS."""
        cutoff = datetime.now(UTC) - timedelta(days=self.INBOX_CLEANUP_AFTER_DAYS)
        result = await self.db.execute(
            """
            DELETE FROM memory_inbox
            WHERE status IN ('approved', 'merged', 'rejected')
              AND processed_at < $1
            """,
            cutoff,
        )
        # asyncpg returns "DELETE N" string.
        try:
            deleted = int(str(result).split()[-1])
        except (ValueError, IndexError):
            deleted = 0
        return {"inbox_rows_deleted": deleted}
