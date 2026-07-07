"""Persistence + queries for the entity graph.

Entities are upserted by (name, entity_type); links and relations are upserted
by their natural keys so re-observing the same fact is idempotent (relations
additionally reinforce: evidence_count grows, last_seen_at advances). Read
methods return search_memory-shaped rows so the graph plugs into existing agent
result handling.
"""

from typing import Any, Protocol


class _DB(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def fetchall(self, query: str, *args: Any) -> list[Any]: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    async def execute(self, query: str, *args: Any) -> Any: ...


class GraphStore:
    """Upsert entities/links/relations and answer entity-scoped queries."""

    async def upsert_entity(
        self,
        db: _DB,
        name: str,
        entity_type: str,
        aliases: list[str] | None = None,
    ) -> str:
        """Insert or fetch an entity by (name, entity_type); returns its id.

        On conflict the aliases are unioned in (never dropped) so repeated
        extractions accumulate spellings rather than clobbering them.
        """
        entity_id = await db.fetchval(
            """
            INSERT INTO entity (name, entity_type, aliases)
            VALUES ($1, $2, $3::text[])
            ON CONFLICT (name, entity_type) DO UPDATE SET
                aliases = (
                    SELECT ARRAY(
                        SELECT DISTINCT unnest(entity.aliases || EXCLUDED.aliases)
                    )
                )
            RETURNING id
            """,
            name,
            entity_type,
            aliases or [],
        )
        return str(entity_id)

    async def link_memory(
        self,
        db: _DB,
        memory_id: str,
        entity_id: str,
        relation: str = "mentions",
        confidence: float = 1.0,
    ) -> None:
        """Link a memory to an entity; idempotent on (memory, entity, relation)."""
        await db.execute(
            """
            INSERT INTO memory_entity_link (memory_id, entity_id, relation, confidence)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (memory_id, entity_id, relation) DO NOTHING
            """,
            memory_id,
            entity_id,
            relation,
            confidence,
        )

    async def record_relation(
        self,
        db: _DB,
        subject_id: str,
        predicate: str,
        object_id: str,
        confidence: float = 1.0,
    ) -> None:
        """Upsert an entity↔entity edge; re-observing reinforces the evidence."""
        await db.execute(
            """
            INSERT INTO entity_relation (subject_id, predicate, object_id, confidence)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (subject_id, predicate, object_id) DO UPDATE SET
                evidence_count = entity_relation.evidence_count + 1,
                last_seen_at = now(),
                confidence = GREATEST(entity_relation.confidence, EXCLUDED.confidence)
            """,
            subject_id,
            predicate,
            object_id,
            confidence,
        )

    async def search_by_entity(
        self, db: _DB, entity_name: str, limit: int = 20
    ) -> list[dict]:
        """Memories mentioning an entity (fuzzy match on name or aliases).

        Returns search_memory-shaped rows so callers can treat graph hits and
        text hits uniformly.
        """
        limit = max(1, min(int(limit), 50))
        rows = await db.fetchall(
            """
            SELECT DISTINCT m.id, m.content, m.trust_score, m.memory_type,
                   m.created_at, m.created_by, m.source_agent
            FROM memory_entry m
            JOIN memory_entity_link mel ON mel.memory_id = m.id
            JOIN entity e ON e.id = mel.entity_id
            WHERE m.valid_until IS NULL
              AND (e.name ILIKE $1 OR $2 = ANY(e.aliases))
            ORDER BY m.created_at DESC
            LIMIT $3
            """,
            f"%{entity_name}%",
            entity_name,
            limit,
        )
        return [
            {
                "id": str(r["id"]),
                "content": r["content"],
                "trust_score": float(r["trust_score"]),
                "memory_type": r["memory_type"],
                "created_at": r["created_at"].isoformat(),
                "source": r["source_agent"] or r["created_by"],
            }
            for r in rows
        ]

    async def get_entity_graph(self, db: _DB, entity_name: str) -> dict:
        """The entity, its relations (both directions), and its linked-memory count."""
        entity = await db.fetchrow(
            """
            SELECT id, name, entity_type, aliases, metadata, created_at
            FROM entity
            WHERE name ILIKE $1 OR $2 = ANY(aliases)
            ORDER BY name = $2 DESC
            LIMIT 1
            """,
            f"%{entity_name}%",
            entity_name,
        )
        if entity is None:
            return {"entity": None, "relations": [], "memory_count": 0}

        entity_id = entity["id"]
        relations = await db.fetchall(
            """
            SELECT er.predicate, er.evidence_count, er.confidence,
                   subj.name AS subject_name, obj.name AS object_name,
                   (er.subject_id = $1) AS outgoing
            FROM entity_relation er
            JOIN entity subj ON subj.id = er.subject_id
            JOIN entity obj ON obj.id = er.object_id
            WHERE er.subject_id = $1 OR er.object_id = $1
            ORDER BY er.evidence_count DESC
            """,
            entity_id,
        )
        memory_count = await db.fetchval(
            "SELECT count(*) FROM memory_entity_link WHERE entity_id = $1",
            entity_id,
        )
        return {
            "entity": {
                "id": str(entity["id"]),
                "name": entity["name"],
                "entity_type": entity["entity_type"],
                "aliases": list(entity["aliases"] or []),
            },
            "relations": [
                {
                    "predicate": r["predicate"],
                    "subject": r["subject_name"],
                    "object": r["object_name"],
                    "evidence_count": int(r["evidence_count"]),
                    "confidence": float(r["confidence"]),
                    "direction": "outgoing" if r["outgoing"] else "incoming",
                }
                for r in relations
            ],
            "memory_count": int(memory_count or 0),
        }
