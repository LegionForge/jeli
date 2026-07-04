"""ConflictResolverDaemon — drains memory_conflict_queue.

N instances safe: each row is claimed via FOR UPDATE SKIP LOCKED.
pg_notify wakes sleeping instances; they fall through to the queue drain.
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

import asyncpg

from ..core.contradiction import ContradictionClassifier, ContradictionDetector, ContradictionSeverity
from ..database.pool import AsyncPostgresPool
from ..embedding.provider import EmbeddingProvider

logger = logging.getLogger(__name__)

NOTIFY_CHANNEL = "jeli_memory_written"
NEIGHBOR_LIMIT = 5
POLL_INTERVAL_SECONDS = 10.0
CHAIN_WRITE_LOCK = 0x4A454C49


class ConflictResolverDaemon:
    def __init__(
        self,
        db: AsyncPostgresPool,
        embedder: EmbeddingProvider,
        chain_key: str,
        key_id: str = "k1",
        worker_id: str | None = None,
        instance_index: int = 0,
    ):
        self.db = db
        self.embedder = embedder
        self.chain_key = chain_key
        self.key_id = key_id
        self.worker_id = worker_id or f"conflict-resolver-{uuid.uuid4().hex[:8]}"
        self.instance_index = instance_index
        self._notify_event = asyncio.Event()

    async def run_forever(self):
        logger.info(
            "conflict resolver %s (index=%d) started", self.worker_id, self.instance_index
        )
        if not self.db.pool:
            raise RuntimeError("DB pool not connected")

        # Dedicated connection for LISTEN — must not be returned to pool.
        listen_conn: asyncpg.Connection = await self.db.pool.acquire()
        try:
            await listen_conn.add_listener(NOTIFY_CHANNEL, self._on_notify)

            while True:
                try:
                    processed = await self._drain_queue()
                    if processed == 0:
                        # Nothing in queue — wait for a notify or timeout.
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(self._notify_event.wait()),
                                timeout=POLL_INTERVAL_SECONDS,
                            )
                        except asyncio.TimeoutError:
                            pass
                        finally:
                            self._notify_event.clear()
                except asyncio.CancelledError:
                    logger.info("conflict resolver %s cancelled", self.worker_id)
                    return
                except Exception:
                    logger.exception("conflict resolver %s error", self.worker_id)
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
        finally:
            await listen_conn.remove_listener(NOTIFY_CHANNEL, self._on_notify)
            await self.db.pool.release(listen_conn)

    def _on_notify(self, conn, pid, channel, payload):
        self._notify_event.set()

    async def _drain_queue(self) -> int:
        processed = 0
        while True:
            row = await self._claim_one()
            if row is None:
                break
            await self._handle_queue_row(dict(row))
            processed += 1
        return processed

    async def _claim_one(self):
        if not self.db.pool:
            return None
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE memory_conflict_queue
                    SET status = 'processing',
                        claimed_by = $1,
                        claimed_at = now()
                    WHERE id = (
                        SELECT id FROM memory_conflict_queue
                        WHERE status = 'pending'
                        ORDER BY enqueued_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING *
                    """,
                    self.worker_id,
                )
        return row

    async def _handle_queue_row(self, row: dict) -> None:
        queue_id = str(row["id"])
        memory_id = str(row["memory_id"])
        try:
            flags_found = await self._check_memory(memory_id)
            await self.db.execute(
                """
                UPDATE memory_conflict_queue
                SET status = 'done', finished_at = $1, flags_found = $2
                WHERE id = $3
                """,
                datetime.now(UTC),
                flags_found,
                queue_id,
            )
        except Exception as exc:
            logger.exception("conflict check failed for memory %s", memory_id)
            retry = (row.get("retry_count") or 0) + 1
            new_status = "failed" if retry >= 3 else "pending"
            await self.db.execute(
                """
                UPDATE memory_conflict_queue
                SET status = $1, error = $2, retry_count = $3, claimed_by = NULL
                WHERE id = $4
                """,
                new_status,
                str(exc)[:500],
                retry,
                queue_id,
            )

    async def _check_memory(self, memory_id: str) -> int:
        new_row = await self.db.fetchrow(
            """
            SELECT id, content, trust_score, memory_type, created_at, embedding
            FROM memory_entry WHERE id = $1
            """,
            memory_id,
        )
        if new_row is None:
            return 0

        try:
            q_embedding = await self.embedder.embed_query(new_row["content"])
        except Exception:
            logger.warning("embed failed for conflict check on %s", memory_id, exc_info=True)
            return 0

        neighbors = await self.db.fetchall(
            """
            SELECT id, content, trust_score, memory_type, created_at
            FROM memory_entry
            WHERE valid_until IS NULL
              AND id != $1
            ORDER BY embedding <=> $2::vector
            LIMIT $3
            """,
            memory_id,
            json.dumps(q_embedding.vector),
            NEIGHBOR_LIMIT,
        )

        flags_count = 0
        new_mem = {
            "id": str(new_row["id"]),
            "content": new_row["content"],
            "trust_score": float(new_row["trust_score"]),
            "memory_type": new_row["memory_type"],
        }

        for neighbor in neighbors:
            old_mem = {
                "id": str(neighbor["id"]),
                "content": neighbor["content"],
                "trust_score": float(neighbor["trust_score"]),
                "memory_type": neighbor["memory_type"],
            }
            similarity = ContradictionDetector.detect_semantic_similarity(
                old_mem["content"], new_mem["content"]
            )
            flags = ContradictionClassifier.classify(old_mem, new_mem, similarity_score=similarity)

            for flag in flags:
                flags_count += 1
                if flag.severity == ContradictionSeverity.HIGH:
                    await self._resolve_high(new_mem, old_mem, flag.reason)
                elif flag.severity == ContradictionSeverity.MEDIUM:
                    await self._log_conflict(new_mem["id"], old_mem["id"], flag.reason, "medium")

        return flags_count

    async def _resolve_high(self, new_mem: dict, old_mem: dict, reason: str) -> None:
        """Deterministic resolution: higher trust wins; newer wins on tie."""
        new_trust = new_mem["trust_score"]
        old_trust = old_mem["trust_score"]
        loser_id = old_mem["id"] if new_trust >= old_trust else new_mem["id"]

        from ..core.hash_chain import build_canonical_record, compute_record_hash
        from ..tools.state_tools import StateTools
        from ..tools.memory_tools import MemoryTools

        # Use StateTools to invalidate the loser — this writes a chained state event.
        tools = MemoryTools(db=self.db, embedder=None, chain_key=self.chain_key, key_id=self.key_id)
        state = StateTools(db=self.db, memory_tools=tools, chain_key=self.chain_key, key_id=self.key_id)
        await state.invalidate(
            memory_id=loser_id,
            reason=f"conflict-resolver: {reason}",
            actor=f"conflict-resolver/{self.worker_id}",
        )
        logger.info(
            "conflict resolver: invalidated %s (reason: %s)", loser_id, reason
        )

    async def _log_conflict(
        self, memory_id: str, conflicting_id: str, reason: str, severity: str
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO memory_audit_log (memory_id, action, actor, details)
            VALUES ($1, 'conflict_flagged', $2, $3::jsonb)
            """,
            memory_id,
            f"conflict-resolver/{self.worker_id}",
            json.dumps(
                {
                    "conflicting_memory_id": conflicting_id,
                    "reason": reason,
                    "severity": severity,
                }
            ),
        )
