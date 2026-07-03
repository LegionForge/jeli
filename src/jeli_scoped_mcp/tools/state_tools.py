"""User-tier temporal state operations: revise and invalidate.

Per the spec these are USER-ONLY — they are deliberately NOT exposed as MCP
tools (agents propose; humans retire truth). Every state change is recorded
as an event in memory_state_event's own HMAC chain; the mutable columns on
memory_entry (valid_until / superseded_by) are a queryable cache whose
authority is the event chain. verify() cross-checks cache against chain, so
an attacker who flips the columns without the key is detected.
"""

import logging
from typing import Any

from ..core.hash_chain import HashChainValidator, canonical_json, compute_record_hash
from ..database.pool import AsyncPostgresPool
from ..tools.memory_tools import MemoryToolError, MemoryTools

logger = logging.getLogger(__name__)

# Separate advisory lock from the memory chain — the two chains are independent.
STATE_CHAIN_LOCK = 0x4A454C53  # "JELS"


def build_canonical_state_event(
    event_type: str,
    target_memory_id: str,
    successor_memory_id: str | None,
    reason: str,
    valid_until: str,
    key_id: str,
) -> str:
    """Canonical JSON for a state event (fields that define the event)."""
    return canonical_json(
        {
            "event_type": event_type,
            "target_memory_id": str(target_memory_id),
            "successor_memory_id": (str(successor_memory_id) if successor_memory_id else None),
            "reason": reason,
            "valid_until": valid_until,
            "key_id": key_id,
        }
    )


class StateTools:
    """revise / invalidate + state-chain verification."""

    def __init__(
        self,
        db: AsyncPostgresPool,
        memory_tools: MemoryTools,
        chain_key: str,
        key_id: str = "k1",
        key_registry: dict[str, str] | None = None,
    ):
        self.db = db
        self.memory = memory_tools
        self.chain_key = chain_key
        self.key_id = key_id
        self.key_registry = dict(key_registry or {})
        self.key_registry.setdefault(key_id, chain_key)

    # ── operations ───────────────────────────────────────────────────────────

    async def invalidate(self, memory_id: str, reason: str, actor: str) -> dict:
        """Retire a memory (never delete): chained event + valid_until cache."""
        return await self._apply_event("invalidated", memory_id, None, reason, actor)

    async def revise(
        self,
        memory_id: str,
        new_content: str,
        reason: str,
        actor: str,
        trust_score: float = 0.9,
    ) -> dict:
        """Append a corrected successor, then supersede the original.

        The successor is a first-class memory (amended_from set); the
        supersession is a chained state event. Original stays verbatim.
        """
        old = await self.db.fetchrow(
            "SELECT id, memory_type, valid_until FROM memory_entry WHERE id = $1",
            memory_id,
        )
        if old is None:
            raise MemoryToolError(f"memory {memory_id} not found")
        if old["valid_until"] is not None:
            raise MemoryToolError(f"memory {memory_id} is already retired")

        successor = await self.memory.capture_memory(
            content=new_content,
            memory_type=old["memory_type"],
            trust_score=trust_score,
            actor=actor,
            source_agent=actor,
            metadata={"revises": str(memory_id), "reason": reason},
        )
        await self.db.execute(
            "UPDATE memory_entry SET amended_from = $1 WHERE id = $2",
            memory_id,
            successor["id"],
        )
        event = await self._apply_event("superseded", memory_id, successor["id"], reason, actor)
        return {"successor": successor, "event": event}

    # ── verification ─────────────────────────────────────────────────────────

    async def verify(self) -> dict:
        """Walk the state-event chain, then cross-check the column cache."""
        rows = await self.db.fetchall("""
            SELECT id, event_type, target_memory_id, successor_memory_id,
                   reason, valid_until, prev_hash, record_hash, key_id
            FROM memory_state_event ORDER BY chain_seq ASC
            """)
        prev: str | None = None
        latest: dict[str, dict] = {}
        for row in rows:
            key = self.key_registry.get(row["key_id"])
            if key is None or not HashChainValidator(key).validate_record(
                self._canonical(row), row["record_hash"], prev
            ):
                return {
                    "state_chain_valid": False,
                    "events_checked": len(rows),
                    "first_bad_event": str(row["id"]),
                    "cache_consistent": None,
                }
            prev = row["record_hash"]
            latest[str(row["target_memory_id"])] = row

        # cache cross-check: every retired memory must be justified by an
        # event, and every event must be reflected in the columns
        mismatches = []
        retired = await self.db.fetchall(
            "SELECT id, valid_until, superseded_by FROM memory_entry "
            "WHERE valid_until IS NOT NULL OR superseded_by IS NOT NULL"
        )
        for m in retired:
            ev = latest.get(str(m["id"]))
            if ev is None:
                mismatches.append(f"{m['id']}: retired in columns, no chained event")
                continue
            ev_succ = str(ev["successor_memory_id"]) if ev["successor_memory_id"] else None
            col_succ = str(m["superseded_by"]) if m["superseded_by"] else None
            if ev_succ != col_succ:
                mismatches.append(f"{m['id']}: successor cache != event")
        for tid in latest:
            row = next((m for m in retired if str(m["id"]) == tid), None)
            if row is None:
                mismatches.append(f"{tid}: chained event exists, columns not set")

        return {
            "state_chain_valid": True,
            "events_checked": len(rows),
            "first_bad_event": None,
            "cache_consistent": not mismatches,
            "mismatches": mismatches[:20],
        }

    # ── internals ────────────────────────────────────────────────────────────

    def _canonical(self, row: Any) -> str:
        return build_canonical_state_event(
            event_type=row["event_type"],
            target_memory_id=row["target_memory_id"],
            successor_memory_id=row["successor_memory_id"],
            reason=row["reason"],
            valid_until=row["valid_until"].isoformat(),
            key_id=row["key_id"],
        )

    async def _apply_event(
        self,
        event_type: str,
        target_id: str,
        successor_id: str | None,
        reason: str,
        actor: str,
    ) -> dict:
        if not reason or not reason.strip():
            raise MemoryToolError("a reason is required — state changes are receipts")
        if not actor:
            raise MemoryToolError("actor is required for provenance")

        async with self.db.locked_transaction(STATE_CHAIN_LOCK) as conn:
            target = await conn.fetchrow(
                "SELECT id, valid_until FROM memory_entry WHERE id = $1", target_id
            )
            if target is None:
                raise MemoryToolError(f"memory {target_id} not found")
            if target["valid_until"] is not None:
                raise MemoryToolError(f"memory {target_id} is already retired")

            prev_hash = await conn.fetchval(
                "SELECT record_hash FROM memory_state_event " "ORDER BY chain_seq DESC LIMIT 1"
            )
            valid_until = await conn.fetchval("SELECT now()")
            canonical = build_canonical_state_event(
                event_type,
                target_id,
                successor_id,
                reason,
                valid_until.isoformat(),
                self.key_id,
            )
            record_hash = compute_record_hash(self.chain_key, canonical, prev_hash)

            row = await conn.fetchrow(
                """
                INSERT INTO memory_state_event (
                    event_type, target_memory_id, successor_memory_id, reason,
                    actor, valid_until, prev_hash, record_hash, key_id
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                RETURNING id, created_at
                """,
                event_type,
                target_id,
                successor_id,
                reason,
                actor,
                valid_until,
                prev_hash,
                record_hash,
                self.key_id,
            )
            # cache columns (authority is the event just chained)
            await conn.execute(
                "UPDATE memory_entry SET valid_until = $1, superseded_by = $2 " "WHERE id = $3",
                valid_until,
                successor_id,
                target_id,
            )
            await conn.execute(
                """
                INSERT INTO memory_audit_log (memory_id, action, actor, details)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                target_id,
                event_type,
                actor,
                canonical_json({"reason": reason, "event_id": str(row["id"])}),
            )

        logger.info("%s: memory=%s by=%s reason=%s", event_type, target_id, actor, reason)
        return {
            "event_id": str(row["id"]),
            "event_type": event_type,
            "target": str(target_id),
            "successor": str(successor_id) if successor_id else None,
            "record_hash": record_hash,
        }
