"""Judicial precedent: settled case law keyed by conflict pattern.

A precedent records how a *class* of conflict was resolved — not a single pair
of memories, but the pattern (the two memory types plus the contradiction
type). This key is intentionally too coarse to decide a concrete winner, so
precedent is advisory: the resolver derives each outcome from current evidence
and records whether it agrees or dissents. Agreement nudges confidence toward
1.0; disagreement erodes it and can eventually overturn settled case law.

Confidence growth is gated on *distinct* corroborating sources (GH #44), not
raw agreement count — see judicial_precedent_corroboration and record()/
reinforce() below. Without this, an attacker walking one identity through
repeated similar conflicts could manufacture "settled" precedent alone.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

# Each reinforcement nudges confidence up by this much, capped at 1.0. Slow on
# purpose: a rule earns trust over many consistent applications, not one.
CONFIDENCE_STEP = 0.1
CONFIDENCE_CEILING = 1.0

# A disagreeing deliberation erodes confidence by CONFIDENCE_STEP; the standing
# resolution is kept until erosion drops confidence below this floor, at which
# point the precedent is overturned: the new resolution takes over at the base
# confidence and the applied count restarts. One dissent never flips case law.
OVERTURN_FLOOR = 0.3

# Advisory-lock namespace for precedent read-modify-write. Distinct from
# memory_tools.CHAIN_WRITE_LOCK / state_tools.STATE_CHAIN_LOCK — this guards
# judicial_precedent + judicial_precedent_corroboration only.
JUDICIAL_PRECEDENT_LOCK = 0x4A454C50  # "JELP"

# source_key used when the caller has no better identity to attribute an
# agreement to. Kept distinct from any real agent name so it never silently
# merges with a legitimate source's corroboration history.
UNKNOWN_SOURCE = "unknown-source"


class _DB(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def fetchall(self, query: str, *args: Any) -> list[Any]: ...
    async def execute(self, query: str, *args: Any) -> Any: ...
    def locked_transaction(self, lock_key: int) -> Any: ...


@dataclass
class JudicialPrecedent:
    """A settled rule for one conflict pattern."""

    id: str
    contradiction_type: str
    pattern_hash: str
    resolution: str
    winner_rule: str
    confidence: float
    applied_count: int
    first_set_at: datetime | None = None
    last_applied_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> "JudicialPrecedent":
        return cls(
            id=str(row["id"]),
            contradiction_type=row["contradiction_type"],
            pattern_hash=row["pattern_hash"],
            resolution=row["resolution"],
            winner_rule=row["winner_rule"],
            confidence=float(row["confidence"]),
            applied_count=int(row["applied_count"]),
            first_set_at=row["first_set_at"] if "first_set_at" in row else None,
            last_applied_at=row["last_applied_at"] if "last_applied_at" in row else None,
        )


class PrecedentStore:
    """Lookup / record / reinforce settled case law."""

    @staticmethod
    def pattern_hash(contradiction_type: str, type_a: str, type_b: str) -> str:
        """Stable key for a conflict pattern.

        Symmetric in the two memory types — a preference-vs-identity conflict is
        the same pattern as identity-vs-preference — so the types are sorted
        before hashing.
        """
        lo, hi = sorted((type_a, type_b))
        payload = f"{contradiction_type}\x1f{lo}\x1f{hi}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def lookup(self, db: _DB, phash: str) -> JudicialPrecedent | None:
        row = await db.fetchrow(
            "SELECT * FROM judicial_precedent WHERE pattern_hash = $1",
            phash,
        )
        return JudicialPrecedent.from_row(row) if row is not None else None

    async def _seen_source(self, conn: Any, precedent_id: str, source_key: str) -> bool:
        row = await conn.fetchrow(
            "SELECT 1 FROM judicial_precedent_corroboration "
            "WHERE precedent_id = $1 AND source_key = $2",
            precedent_id,
            source_key,
        )
        return row is not None

    async def _record_source(self, conn: Any, precedent_id: str, source_key: str) -> None:
        await conn.execute(
            "INSERT INTO judicial_precedent_corroboration (precedent_id, source_key) "
            "VALUES ($1, $2) ON CONFLICT (precedent_id, source_key) DO NOTHING",
            precedent_id,
            source_key,
        )

    async def record(
        self,
        db: _DB,
        phash: str,
        contradiction_type: str,
        resolution: str,
        winner_rule: str,
        source_key: str = UNKNOWN_SOURCE,
        confidence: float = 0.5,
    ) -> JudicialPrecedent:
        """Insert a new precedent, or fold a fresh deliberation into the row.

        UNIQUE(pattern_hash) means repeat deliberations of the same pattern land
        on the existing row, with real case-law semantics:

        - **Agreement** (same resolution): applied_count always grows, but
          confidence only climbs the first time a given source_key agrees
          (GH #44 corroboration gate) — repeat agreement from one actor
          doesn't compound; a *new* distinct actor agreeing does.
        - **Disagreement**: the standing resolution is KEPT and confidence
          erodes by one step. A single dissent never rewrites settled law.
        - **Overturn**: once erosion would drop confidence below OVERTURN_FLOOR,
          the new resolution replaces the old at base confidence, the applied
          count restarts at 1, and prior corroboration history for this
          pattern is superseded by the new source_key's first agreement.

        Locked (JUDICIAL_PRECEDENT_LOCK): the read-check-write against both
        tables must be atomic, or two concurrent resolvers can each observe
        "new source" for the same source_key and double-count a confidence step.
        """
        async with db.locked_transaction(JUDICIAL_PRECEDENT_LOCK) as conn:
            existing_row = await conn.fetchrow(
                "SELECT * FROM judicial_precedent WHERE pattern_hash = $1", phash
            )
            if existing_row is None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO judicial_precedent
                        (pattern_hash, contradiction_type, resolution, winner_rule, confidence)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING *
                    """,
                    phash,
                    contradiction_type,
                    resolution,
                    winner_rule,
                    confidence,
                )
                precedent = JudicialPrecedent.from_row(row)
                await self._record_source(conn, precedent.id, source_key)
                return precedent

            existing = JudicialPrecedent.from_row(existing_row)
            agrees = existing.resolution == resolution
            new_source = not await self._seen_source(conn, existing.id, source_key)

            if agrees:
                new_count = existing.applied_count + 1
                new_confidence = (
                    min(CONFIDENCE_CEILING, existing.confidence + CONFIDENCE_STEP)
                    if new_source
                    else existing.confidence
                )
                new_resolution, new_winner = existing.resolution, existing.winner_rule
            elif existing.confidence - CONFIDENCE_STEP < OVERTURN_FLOOR:
                new_count = 1
                new_confidence = confidence
                new_resolution, new_winner = resolution, winner_rule
            else:
                new_count = existing.applied_count
                new_confidence = max(0.0, existing.confidence - CONFIDENCE_STEP)
                new_resolution, new_winner = existing.resolution, existing.winner_rule

            row = await conn.fetchrow(
                """
                UPDATE judicial_precedent
                SET applied_count = $1,
                    confidence = $2,
                    resolution = $3,
                    winner_rule = $4,
                    last_applied_at = now()
                WHERE pattern_hash = $5
                RETURNING *
                """,
                new_count,
                new_confidence,
                new_resolution,
                new_winner,
                phash,
            )
            precedent = JudicialPrecedent.from_row(row)
            await self._record_source(conn, precedent.id, source_key)
            return precedent

    async def reinforce(
        self, db: _DB, precedent_id: str, source_key: str = UNKNOWN_SOURCE
    ) -> None:
        """A precedent was applied again: bump count always; bump confidence
        only if source_key hasn't corroborated this precedent before (GH #44).
        """
        async with db.locked_transaction(JUDICIAL_PRECEDENT_LOCK) as conn:
            new_source = not await self._seen_source(conn, precedent_id, source_key)
            if new_source:
                await conn.execute(
                    """
                    UPDATE judicial_precedent
                    SET applied_count = applied_count + 1,
                        confidence = LEAST($1, confidence + $2),
                        last_applied_at = now()
                    WHERE id = $3
                    """,
                    CONFIDENCE_CEILING,
                    CONFIDENCE_STEP,
                    precedent_id,
                )
            else:
                await conn.execute(
                    """
                    UPDATE judicial_precedent
                    SET applied_count = applied_count + 1,
                        last_applied_at = now()
                    WHERE id = $1
                    """,
                    precedent_id,
                )
            await self._record_source(conn, precedent_id, source_key)

    async def list_precedents(self, db: _DB) -> list[JudicialPrecedent]:
        rows = await db.fetchall(
            "SELECT * FROM judicial_precedent ORDER BY applied_count DESC, confidence DESC"
        )
        return [JudicialPrecedent.from_row(r) for r in rows]
