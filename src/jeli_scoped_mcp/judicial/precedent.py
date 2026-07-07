"""Judicial precedent: settled case law keyed by conflict pattern.

A precedent records how a *class* of conflict was resolved — not a single pair
of memories, but the pattern (the two memory types plus the contradiction
type). The resolver looks a pattern up before re-deliberating; a precedent
whose confidence has crossed the apply threshold is reinforced rather than
re-derived, and each reinforcement nudges confidence toward 1.0.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

# Each reinforcement nudges confidence up by this much, capped at 1.0. Slow on
# purpose: a rule earns trust over many consistent applications, not one.
CONFIDENCE_STEP = 0.1
CONFIDENCE_CEILING = 1.0


class _DB(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def fetchall(self, query: str, *args: Any) -> list[Any]: ...
    async def execute(self, query: str, *args: Any) -> Any: ...


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

    async def record(
        self,
        db: _DB,
        phash: str,
        contradiction_type: str,
        resolution: str,
        winner_rule: str,
        confidence: float = 0.5,
    ) -> JudicialPrecedent:
        """Insert a new precedent, or reinforce an existing one for this pattern.

        UNIQUE(pattern_hash) means a second fresh deliberation of the same
        pattern folds into the existing row: applied_count grows and confidence
        climbs toward the apply threshold, so repeated agreement is what earns a
        precedent its authority.
        """
        row = await db.fetchrow(
            """
            INSERT INTO judicial_precedent
                (pattern_hash, contradiction_type, resolution, winner_rule, confidence)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (pattern_hash) DO UPDATE SET
                applied_count = judicial_precedent.applied_count + 1,
                confidence = LEAST($6, judicial_precedent.confidence + $7),
                resolution = EXCLUDED.resolution,
                winner_rule = EXCLUDED.winner_rule,
                last_applied_at = now()
            RETURNING *
            """,
            phash,
            contradiction_type,
            resolution,
            winner_rule,
            confidence,
            CONFIDENCE_CEILING,
            CONFIDENCE_STEP,
        )
        return JudicialPrecedent.from_row(row)

    async def reinforce(self, db: _DB, precedent_id: str) -> None:
        """A precedent was applied again: bump count, confidence, timestamp."""
        await db.execute(
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

    async def list_precedents(self, db: _DB) -> list[JudicialPrecedent]:
        rows = await db.fetchall(
            "SELECT * FROM judicial_precedent ORDER BY applied_count DESC, confidence DESC"
        )
        return [JudicialPrecedent.from_row(r) for r in rows]
