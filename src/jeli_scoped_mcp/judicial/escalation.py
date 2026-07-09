"""Human escalation queue: conflicts the Judicial branch cannot settle.

When the resolver hits a conflict it will not decide on its own — a MEDIUM
contradiction re-flagged again and again without settling — it enqueues the
pair here for the user (the appellate process). Entries are append-only:
resolving one stamps resolved_at / resolution / resolved_by; nothing is deleted.
"""

from typing import Any, Protocol


class _DB(Protocol):
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    async def fetchall(self, query: str, *args: Any) -> list[Any]: ...
    async def execute(self, query: str, *args: Any) -> Any: ...


class HumanEscalationQueue:
    """Enqueue / list / resolve conflicts awaiting user judgement."""

    async def enqueue(
        self,
        db: _DB,
        memory_id_a: str,
        memory_id_b: str,
        contradiction_type: str,
        reason: str,
        severity: str,
    ) -> str:
        """Add a conflict to the queue; returns the new entry id."""
        entry_id = await db.fetchval(
            """
            INSERT INTO judicial_human_queue
                (memory_id_a, memory_id_b, contradiction_type, reason, severity)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            memory_id_a,
            memory_id_b,
            contradiction_type,
            reason,
            severity,
        )
        return str(entry_id)

    async def list_pending(self, db: _DB) -> list[dict]:
        rows = await db.fetchall(
            """
            SELECT id, memory_id_a, memory_id_b, contradiction_type,
                   reason, severity, enqueued_at
            FROM judicial_human_queue
            WHERE resolved_at IS NULL
            ORDER BY enqueued_at ASC
            """
        )
        return [
            {
                "id": str(r["id"]),
                "memory_id_a": str(r["memory_id_a"]),
                "memory_id_b": str(r["memory_id_b"]),
                "contradiction_type": r["contradiction_type"],
                "reason": r["reason"],
                "severity": r["severity"],
                "enqueued_at": r["enqueued_at"].isoformat(),
            }
            for r in rows
        ]

    async def resolve(
        self, db: _DB, entry_id: str, resolution: str, resolved_by: str
    ) -> None:
        """Mark a pending entry resolved with the user's decision."""
        result = await db.execute(
            """
            UPDATE judicial_human_queue
            SET resolved_at = now(), resolution = $1, resolved_by = $2
            WHERE id = $3 AND resolved_at IS NULL
            """,
            resolution,
            resolved_by,
            entry_id,
        )
        if result == "UPDATE 0":
            raise ValueError(f"queue entry {entry_id} not found or already resolved")
