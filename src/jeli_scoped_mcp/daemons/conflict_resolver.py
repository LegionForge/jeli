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

from ..core.contradiction import (
    ContradictionClassifier,
    ContradictionDetector,
    ContradictionSeverity,
)
from ..database.pool import AsyncPostgresPool
from ..embedding.provider import EmbeddingProvider

logger = logging.getLogger(__name__)

NOTIFY_CHANNEL = "jeli_memory_written"
NEIGHBOR_LIMIT = 5
POLL_INTERVAL_SECONDS = 10.0
CHAIN_WRITE_LOCK = 0x4A454C49
# At or above this trust a memory is user-tier (user-stated / user-confirmed).
# A recency tie must not auto-invalidate one; it escalates to the user instead.
USER_TIER_TRUST = 0.9


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
                        except TimeoutError:
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
                           -- Reclaim claims abandoned by dead workers (same
                           -- 1-hour threshold `jeli verify` counts as stuck).
                           OR (status = 'processing'
                               AND claimed_at < now() - interval '1 hour')
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
            SELECT id, content, trust_score, memory_type, created_at, embedding, source_agent
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
            SELECT id, content, trust_score, memory_type, created_at, source_agent
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
            "source_agent": new_row["source_agent"],
        }

        for neighbor in neighbors:
            old_mem = {
                "id": str(neighbor["id"]),
                "content": neighbor["content"],
                "trust_score": float(neighbor["trust_score"]),
                "memory_type": neighbor["memory_type"],
                "source_agent": neighbor["source_agent"],
            }
            similarity = ContradictionDetector.detect_semantic_similarity(
                old_mem["content"], new_mem["content"]
            )
            flags = ContradictionClassifier.classify(old_mem, new_mem, similarity_score=similarity)

            for flag in flags:
                flags_count += 1
                ctype = flag.contradiction_type.value
                if flag.severity == ContradictionSeverity.HIGH:
                    await self._resolve_high(new_mem, old_mem, flag.reason, ctype)
                elif flag.severity == ContradictionSeverity.MEDIUM:
                    await self._log_conflict(
                        new_mem["id"], old_mem["id"], flag.reason, "medium", ctype
                    )

        return flags_count

    async def _resolve_high(
        self, new_mem: dict, old_mem: dict, reason: str, contradiction_type: str = "direct"
    ) -> None:
        """Resolve a HIGH conflict via precedent, else deliberate and set precedent.

        The deterministic rule is unchanged (higher trust wins; newer wins on
        tie). What is new is the case-law layer: if a confident precedent already
        covers this conflict pattern it is reinforced rather than re-derived;
        otherwise the fresh outcome is recorded as precedent so it accrues
        authority over repeated agreement.
        """
        from ..judicial.precedent import PrecedentStore

        store = PrecedentStore()
        phash = store.pattern_hash(
            contradiction_type, new_mem["memory_type"], old_mem["memory_type"]
        )

        new_trust = new_mem["trust_score"]
        old_trust = old_mem["trust_score"]
        if new_trust != old_trust:
            resolution = "trust_wins"
            winner_rule = "higher trust_score prevails"
        else:
            resolution = "newer_wins"
            winner_rule = "newer memory prevails on trust tie"
        loser_id = old_mem["id"] if new_trust >= old_trust else new_mem["id"]

        # Guard (GH #37): never let recency auto-invalidate a user-tier memory
        # on a trust tie. A newer record tying a genuine user memory (e.g. an
        # import, or another 1.0 write) would otherwise silently destroy it;
        # that is the user's call, so escalate instead of resolving.
        loser_trust = old_trust if loser_id == old_mem["id"] else new_trust
        if new_trust == old_trust and loser_trust >= USER_TIER_TRUST:
            from ..judicial.escalation import HumanEscalationQueue

            await HumanEscalationQueue().enqueue(
                self.db,
                memory_id_a=str(new_mem["id"]),
                memory_id_b=str(old_mem["id"]),
                contradiction_type=contradiction_type,
                reason=f"user-tier tie, not auto-resolved: {reason}",
                severity="high",
            )
            logger.info(
                "conflict resolver: escalated user-tier tie (%s vs %s) instead "
                "of auto-invalidating",
                new_mem["id"],
                old_mem["id"],
            )
            return

        # Corroboration source for GH #44's Sybil gate: whichever memory's
        # write triggered this deliberation is the "witness" being credited.
        # Falls back to UNKNOWN_SOURCE if the memory has no declared agent.
        from ..judicial.precedent import UNKNOWN_SOURCE

        source_key = new_mem.get("source_agent") or UNKNOWN_SOURCE

        precedent = await store.lookup(self.db, phash)
        precedent_applied = precedent is not None and precedent.confidence >= 0.7
        if precedent_applied:
            await store.reinforce(self.db, precedent.id, source_key)  # type: ignore[union-attr]
        else:
            updated = await store.record(
                self.db, phash, contradiction_type, resolution, winner_rule, source_key
            )
            # An overturn is settled law flipping — rare, high-stakes, and its
            # interaction with the corroboration ledger is deliberately not
            # auto-resolved policy yet (JP, 2026-07-10): surface every overturn
            # for human review alongside the auto-applied outcome.
            if precedent is not None and updated.resolution != precedent.resolution:
                from ..judicial.escalation import HumanEscalationQueue

                await HumanEscalationQueue().enqueue(
                    self.db,
                    memory_id_a=str(new_mem["id"]),
                    memory_id_b=str(old_mem["id"]),
                    contradiction_type=contradiction_type,
                    reason=(
                        f"precedent OVERTURNED: '{precedent.resolution}' -> "
                        f"'{updated.resolution}' (pattern {phash[:12]}, "
                        f"source {source_key}); review the flip and its "
                        f"corroboration history"
                    ),
                    severity="high",
                )
                logger.warning(
                    "conflict resolver: precedent %s overturned (%s -> %s) — "
                    "escalated for human review",
                    phash[:12],
                    precedent.resolution,
                    updated.resolution,
                )

        from ..tools.memory_tools import MemoryTools
        from ..tools.state_tools import StateTools

        # Use StateTools to invalidate the loser — this writes a chained state event.
        tools = MemoryTools(db=self.db, embedder=None, chain_key=self.chain_key, key_id=self.key_id)
        state = StateTools(db=self.db, memory_tools=tools, chain_key=self.chain_key, key_id=self.key_id)
        await state.invalidate(
            memory_id=loser_id,
            reason=f"conflict-resolver: {reason}",
            actor=f"conflict-resolver/{self.worker_id}",
        )
        await self.db.execute(
            """
            INSERT INTO memory_audit_log (memory_id, action, actor, details)
            VALUES ($1, 'conflict_resolved', $2, $3::jsonb)
            """,
            loser_id,
            f"conflict-resolver/{self.worker_id}",
            json.dumps(
                {
                    "reason": reason,
                    "contradiction_type": contradiction_type,
                    "resolution": resolution,
                    "precedent_applied": precedent_applied,
                }
            ),
        )
        logger.info(
            "conflict resolver: invalidated %s (reason: %s, precedent_applied=%s)",
            loser_id,
            reason,
            precedent_applied,
        )

    async def _log_conflict(
        self,
        memory_id: str,
        conflicting_id: str,
        reason: str,
        severity: str,
        contradiction_type: str = "direct",
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

        # A MEDIUM conflict that keeps re-flagging without settling is one the
        # resolver will not decide on its own — escalate it to the user.
        if await self._check_escalation_needed(memory_id):
            from ..judicial.escalation import HumanEscalationQueue

            queue = HumanEscalationQueue()
            entry_id = await queue.enqueue(
                self.db, memory_id, conflicting_id, contradiction_type, reason, severity
            )
            await self.db.execute(
                """
                INSERT INTO memory_audit_log (memory_id, action, actor, details)
                VALUES ($1, 'conflict_escalated', $2, $3::jsonb)
                """,
                memory_id,
                f"conflict-resolver/{self.worker_id}",
                json.dumps({"queue_entry_id": entry_id, "reason": reason}),
            )

    async def _check_escalation_needed(self, memory_id: str) -> bool:
        """True once a memory has been conflict_flagged 3+ times recently."""
        count = await self.db.fetchval(
            """
            SELECT count(*) FROM memory_audit_log
            WHERE memory_id = $1
              AND action = 'conflict_flagged'
              AND created_at > now() - interval '7 days'
            """,
            memory_id,
        )
        return bool(count is not None and count >= 3)
