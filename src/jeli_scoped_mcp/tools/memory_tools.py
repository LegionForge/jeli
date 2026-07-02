"""Scoped memory tools — the only surface agents get.

Implements the Phase 1 tool contract from CLAUDE.md:
  capture_memory  — validated, hash-chained write to the append-only log
  search_memory   — read-only query (fts mode in Phase 1; semantic needs pgvector)
  audit_trail     — provenance chain + integrity verification for one memory
  verify_chain    — walk the whole chain, find the first tampered record

No shell, no file access, no raw SQL from agents. Contradiction detection on
the write path is Phase 3 (see TECHNICAL-SPECIFICATION.md, Next Phases).
"""

import hashlib
import json
import logging
from typing import Any

from ..core.hash_chain import (
    HashChainValidator,
    build_canonical_record,
    compute_record_hash,
)
from ..core.trust_score import TrustScorer
from ..database.pool import AsyncPostgresPool
from ..embedding.provider import EmbeddingProvider
from ..security import InjectionDefense

logger = logging.getLogger(__name__)

# Trust ceiling applied when content matches injection patterns: treated as
# EXTERNAL-grade evidence regardless of what the caller claimed.
FLAGGED_TRUST_CEILING = 0.3

VALID_MEMORY_TYPES = {
    "preference",
    "identity",
    "episodic",
    "semantic",
    "procedural",
    "transient",
}

SEARCH_MODES = {"fts"}  # "semantic" arrives with the pgvector migration


class MemoryToolError(Exception):
    """Raised for invalid tool input; message is safe to return to the agent."""


class MemoryTools:
    """Write/read paths for the scoped MCP tools.

    Phase 1 assumes a single writer (one MCP server process); the prev-hash
    read and insert are not serialized across processes.
    """

    def __init__(
        self,
        db: AsyncPostgresPool,
        embedder: EmbeddingProvider,
        chain_key: str,
    ):
        self.db = db
        self.embedder = embedder
        self.chain_key = chain_key
        self.validator = HashChainValidator(chain_key)

    # ── capture_memory ───────────────────────────────────────────────────────

    async def capture_memory(
        self,
        content: str,
        memory_type: str,
        trust_score: float,
        actor: str,
        source_agent: str | None = None,
        session_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Validate, embed, hash-chain, and append one memory. Returns the
        stored record's id, trust, and hash so the caller can keep a receipt."""
        if not content or not content.strip():
            raise MemoryToolError("content must be non-empty")
        if memory_type not in VALID_MEMORY_TYPES:
            raise MemoryToolError(f"memory_type must be one of {sorted(VALID_MEMORY_TYPES)}")
        if not actor:
            raise MemoryToolError("actor is required for provenance")

        valid, err = TrustScorer.validate(trust_score)
        if not valid:
            raise MemoryToolError(err or "invalid trust_score")
        trust = TrustScorer.clamp(trust_score)

        content, flagged = InjectionDefense.sanitize_content(content)
        meta: dict[str, Any] = dict(metadata or {})
        if flagged:
            # Never block — poisoned-looking content is still evidence. Cap its
            # authority instead so the judicial layer sees it as external-grade.
            trust = min(trust, FLAGGED_TRUST_CEILING)
            meta["injection_flagged"] = True

        embedding = await self.embedder.embed(content)
        if not InjectionDefense.validate_embedding_dimensions(
            embedding.dimensions, embedding.model_id
        ):
            raise MemoryToolError(
                f"embedding dimensions {embedding.dimensions} do not match "
                f"model {embedding.model_id}"
            )

        prev_hash = await self.db.fetchval(
            "SELECT record_hash FROM memory_entry ORDER BY created_at DESC, id DESC LIMIT 1"
        )
        canonical = build_canonical_record(
            content=content,
            embedding_model=embedding.model_id,
            embedding_dimensions=embedding.dimensions,
            trust_score=trust,
            memory_type=memory_type,
            metadata=meta or None,
        )
        record_hash = compute_record_hash(self.chain_key, canonical, prev_hash)

        row = await self.db.fetchrow(
            """
            INSERT INTO memory_entry (
                content, content_hash, embedding, embedding_model,
                embedding_dimensions, embedded_at, metadata, trust_score,
                memory_type, prev_hash, record_hash, created_by,
                session_id, source_agent
            ) VALUES (
                $1, $2, $3::jsonb, $4,
                $5, $6, $7::jsonb, $8,
                $9, $10, $11, $12,
                $13, $14
            )
            RETURNING id, created_at
            """,
            content,
            hashlib.sha256(content.encode()).hexdigest(),
            json.dumps(embedding.vector),
            embedding.model_id,
            embedding.dimensions,
            embedding.embedded_at,
            json.dumps(meta),
            trust,
            memory_type,
            prev_hash,
            record_hash,
            actor,
            session_id,
            source_agent,
        )

        await self.db.execute(
            """
            INSERT INTO memory_audit_log (memory_id, action, actor, source_session, details)
            VALUES ($1, 'created', $2, $3, $4::jsonb)
            """,
            row["id"],
            actor,
            session_id,
            json.dumps(
                {
                    "source_agent": source_agent,
                    "trust_score": trust,
                    "injection_flagged": flagged,
                }
            ),
        )

        logger.info(
            "capture_memory: id=%s type=%s trust=%.2f flagged=%s actor=%s",
            row["id"],
            memory_type,
            trust,
            flagged,
            actor,
        )
        return {
            "id": str(row["id"]),
            "created_at": row["created_at"].isoformat(),
            "trust_score": trust,
            "record_hash": record_hash,
            "injection_flagged": flagged,
        }

    # ── search_memory ────────────────────────────────────────────────────────

    async def search_memory(
        self,
        query: str,
        actor: str,
        mode: str = "fts",
        limit: int = 10,
    ) -> list[dict]:
        """Read-only search over currently-valid memories, ranked by trust
        then recency. Phase 1 supports fts (substring) mode only."""
        if not query or not query.strip():
            raise MemoryToolError("query must be non-empty")
        if mode not in SEARCH_MODES:
            raise MemoryToolError(
                f"mode must be one of {sorted(SEARCH_MODES)} "
                "(semantic search lands with the pgvector migration)"
            )
        limit = max(1, min(int(limit), 50))

        rows = await self.db.fetchall(
            """
            SELECT id, content, trust_score, memory_type, created_at,
                   created_by, source_agent
            FROM memory_entry
            WHERE valid_until IS NULL
              AND content ILIKE '%' || $1 || '%'
            ORDER BY trust_score DESC, created_at DESC
            LIMIT $2
            """,
            query,
            limit,
        )

        results = []
        for r in rows:
            results.append(
                {
                    "id": str(r["id"]),
                    "content": r["content"],
                    "trust_score": float(r["trust_score"]),
                    "memory_type": r["memory_type"],
                    "created_at": r["created_at"].isoformat(),
                    "source": r["source_agent"] or r["created_by"],
                }
            )
            await self.db.execute(
                """
                INSERT INTO memory_audit_log (memory_id, action, actor, details)
                VALUES ($1, 'searched', $2, $3::jsonb)
                """,
                r["id"],
                actor,
                json.dumps({"query": query[:200], "mode": mode}),
            )
        return results

    # ── audit_trail ──────────────────────────────────────────────────────────

    async def audit_trail(self, memory_id: str, actor: str) -> dict:
        """Return one memory's provenance: stored fields, its audit events,
        and whether its record hash still verifies against the chain."""
        row = await self.db.fetchrow(
            """
            SELECT id, content, embedding_model, embedding_dimensions,
                   metadata, trust_score, memory_type, prev_hash, record_hash,
                   created_at, created_by, source_agent, valid_until,
                   superseded_by, amended_from
            FROM memory_entry WHERE id = $1
            """,
            memory_id,
        )
        if row is None:
            raise MemoryToolError(f"memory {memory_id} not found")

        integrity_ok = self._verify_row(row)

        events = await self.db.fetchall(
            """
            SELECT timestamp, action, actor, details
            FROM memory_audit_log WHERE memory_id = $1 ORDER BY timestamp ASC
            """,
            memory_id,
        )
        return {
            "id": str(row["id"]),
            "content": row["content"],
            "memory_type": row["memory_type"],
            "trust_score": float(row["trust_score"]),
            "created_at": row["created_at"].isoformat(),
            "created_by": row["created_by"],
            "source_agent": row["source_agent"],
            "valid": row["valid_until"] is None,
            "superseded_by": str(row["superseded_by"]) if row["superseded_by"] else None,
            "amended_from": str(row["amended_from"]) if row["amended_from"] else None,
            "integrity_verified": integrity_ok,
            "record_hash": row["record_hash"],
            "audit_events": [
                {
                    "timestamp": e["timestamp"].isoformat(),
                    "action": e["action"],
                    "actor": e["actor"],
                    "details": e["details"],
                }
                for e in events
            ],
        }

    # ── verify_chain ─────────────────────────────────────────────────────────

    async def verify_chain(self) -> dict:
        """Walk the full chain oldest→newest, recomputing every hash.
        Returns validity plus the first tampered record id, if any."""
        rows = await self.db.fetchall("""
            SELECT id, content, embedding_model, embedding_dimensions,
                   metadata, trust_score, memory_type, prev_hash, record_hash
            FROM memory_entry ORDER BY created_at ASC, id ASC
            """)
        prev_hash: str | None = None
        for row in rows:
            if not self._verify_row(row, prev_hash=prev_hash, chain_walk=True):
                return {
                    "chain_valid": False,
                    "records_checked": len(rows),
                    "first_bad_record": str(row["id"]),
                }
            prev_hash = row["record_hash"]
        return {
            "chain_valid": True,
            "records_checked": len(rows),
            "first_bad_record": None,
        }

    # ── helpers ──────────────────────────────────────────────────────────────

    def _verify_row(
        self,
        row: Any,
        prev_hash: str | None = None,
        chain_walk: bool = False,
    ) -> bool:
        """Recompute a stored row's canonical hash and compare.

        chain_walk=True means prev_hash is authoritative (even when None,
        i.e. genuinely first in chain); otherwise trust the row's stored
        prev_hash for a single-record check.
        """
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        canonical = build_canonical_record(
            content=row["content"],
            embedding_model=row["embedding_model"],
            embedding_dimensions=row["embedding_dimensions"],
            trust_score=float(row["trust_score"]),
            memory_type=row["memory_type"],
            metadata=meta or None,
        )
        effective_prev = prev_hash if chain_walk else row["prev_hash"]
        return self.validator.validate_record(canonical, row["record_hash"], effective_prev)
