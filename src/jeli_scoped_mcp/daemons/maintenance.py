"""MaintenanceDaemon — trust decay, archival, inbox cleanup."""

import logging
from datetime import UTC, datetime, timedelta

from ..core.trust_score import TrustAdjustment
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
        """Decay agent-inferred memories (trust < 0.9) by 1%/day."""
        rows = await self.db.fetchall(
            """
            SELECT id, trust_score, created_at
            FROM memory_entry
            WHERE valid_until IS NULL
              AND trust_score < 0.9
            ORDER BY created_at ASC
            LIMIT $1
            """,
            self.DECAY_BATCH_SIZE,
        )
        updated = 0
        now = datetime.now(UTC)
        for row in rows:
            days = (now - row["created_at"].replace(tzinfo=UTC)).days
            if days < 1:
                continue
            new_trust = TrustAdjustment.decay_over_time(
                float(row["trust_score"]), days_elapsed=days
            )
            if abs(new_trust - float(row["trust_score"])) < 0.001:
                continue
            try:
                await self.db.execute(
                    "UPDATE memory_entry SET trust_score = $1 WHERE id = $2",
                    round(new_trust, 4),
                    row["id"],
                )
                updated += 1
            except Exception:
                logger.warning("decay: failed to update %s", row["id"], exc_info=True)
        return {"decayed": updated}

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
