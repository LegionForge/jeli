"""InsightsDaemon — nightly cluster scan, stale procedural detection, weak signal flagging."""

import json
import logging
from datetime import UTC, datetime, timedelta

from ..database.pool import AsyncPostgresPool
from ..embedding.provider import EmbeddingProvider
from ..tools.memory_tools import MemoryTools

logger = logging.getLogger(__name__)


class InsightsDaemon:
    MIN_CLUSTER_SIZE = 3
    CLUSTER_DISTANCE_THRESHOLD = 0.25
    STALE_PROCEDURAL_DAYS = 30
    WEAK_SIGNAL_MAX_COUNT = 2
    WEAK_SIGNAL_MAX_TRUST = 0.6

    def __init__(
        self,
        db: AsyncPostgresPool,
        embedder: EmbeddingProvider,
        memory_tools: MemoryTools,
        actor: str = "insights-daemon",
    ):
        self.db = db
        self.embedder = embedder
        self.memory_tools = memory_tools
        self.actor = actor

    async def run_once(self) -> dict:
        results: dict = {}
        results["clusters"] = await self._cluster_scan()
        results["stale_procedural"] = await self._stale_procedural_scan()
        results["weak_signal"] = await self._weak_signal_scan()
        return results

    async def _cluster_scan(self) -> dict:
        """Find semantic clusters and write a summary memory for each."""
        rows = await self.db.fetchall(
            """
            SELECT id, content, memory_type, embedding
            FROM memory_entry
            WHERE valid_until IS NULL
              AND embedding IS NOT NULL
              AND metadata->>'insight_type' IS DISTINCT FROM 'cluster'
            ORDER BY created_at DESC
            LIMIT 2000
            """
        )
        if not rows:
            return {"clusters_found": 0}

        visited: set[str] = set()
        clusters_written = 0

        for row in rows:
            mid = str(row["id"])
            if mid in visited:
                continue
            if row["embedding"] is None:
                continue

            # Find all neighbors within threshold using the DB.
            try:
                embedding_vec = row["embedding"]
                if isinstance(embedding_vec, str):
                    embedding_vec = embedding_vec
                neighbors = await self.db.fetchall(
                    """
                    SELECT id, content
                    FROM memory_entry
                    WHERE valid_until IS NULL
                      AND id != $1
                      AND (embedding <=> $2::vector) < $3
                    ORDER BY embedding <=> $2::vector
                    LIMIT 50
                    """,
                    row["id"],
                    embedding_vec,
                    self.CLUSTER_DISTANCE_THRESHOLD,
                )
            except Exception:
                logger.warning("cluster scan: embedding query failed for %s", mid, exc_info=True)
                continue

            member_ids = [mid] + [str(n["id"]) for n in neighbors]
            if len(member_ids) < self.MIN_CLUSTER_SIZE:
                continue

            for m in member_ids:
                visited.add(m)

            # Write a cluster-summary semantic memory (no LLM — v1 lists members).
            snippets = [row["content"][:50]] + [n["content"][:50] for n in neighbors[:4]]
            summary = "Cluster: " + " | ".join(snippets)

            try:
                await self.memory_tools.capture_memory(
                    content=summary,
                    memory_type="semantic",
                    trust_score=0.5,
                    actor=self.actor,
                    source_agent=self.actor,
                    metadata={
                        "insight_type": "cluster",
                        "cluster_members": member_ids[:20],
                        "daemon": "insights",
                        "generated_at": datetime.now(UTC).isoformat(),
                    },
                )
                clusters_written += 1
            except Exception:
                logger.warning("cluster scan: failed to write cluster memory", exc_info=True)

        return {"clusters_found": clusters_written}

    async def _stale_procedural_scan(self) -> dict:
        """Flag procedural memories not accessed in STALE_PROCEDURAL_DAYS days."""
        cutoff = datetime.now(UTC) - timedelta(days=self.STALE_PROCEDURAL_DAYS)
        rows = await self.db.fetchall(
            """
            SELECT me.id
            FROM memory_entry me
            WHERE me.valid_until IS NULL
              AND me.memory_type = 'procedural'
              AND NOT EXISTS (
                  SELECT 1 FROM memory_audit_log mal
                  WHERE mal.memory_id = me.id
                    AND mal.action = 'searched'
                    AND mal.timestamp > $1
              )
              AND NOT EXISTS (
                  SELECT 1 FROM memory_audit_log mal
                  WHERE mal.memory_id = me.id
                    AND mal.action = 'stale_flagged'
              )
            """,
            cutoff,
        )
        flagged = 0
        for row in rows:
            try:
                await self.db.execute(
                    """
                    INSERT INTO memory_audit_log (memory_id, action, actor, details)
                    VALUES ($1, 'stale_flagged', $2, $3::jsonb)
                    """,
                    row["id"],
                    self.actor,
                    json.dumps({"reason": f"not accessed in {self.STALE_PROCEDURAL_DAYS} days"}),
                )
                flagged += 1
            except Exception:
                logger.warning("stale scan: failed to flag %s", row["id"], exc_info=True)
        return {"stale_procedural_flagged": flagged}

    async def _weak_signal_scan(self) -> dict:
        """Flag topic areas with only 1-2 low-trust memories."""
        rows = await self.db.fetchall(
            """
            SELECT memory_type, COUNT(*) AS cnt, AVG(trust_score) AS avg_trust
            FROM memory_entry
            WHERE valid_until IS NULL
              AND trust_score < $1
            GROUP BY memory_type
            HAVING COUNT(*) <= $2
            """,
            self.WEAK_SIGNAL_MAX_TRUST,
            self.WEAK_SIGNAL_MAX_COUNT,
        )
        flagged = 0
        for row in rows:
            try:
                await self.db.execute(
                    """
                    INSERT INTO memory_audit_log (memory_id, action, actor, details)
                    SELECT id, 'weak_signal_flagged', $1, $2::jsonb
                    FROM memory_entry
                    WHERE valid_until IS NULL
                      AND memory_type = $3
                      AND trust_score < $4
                    """,
                    self.actor,
                    json.dumps(
                        {
                            "reason": "weak signal: few low-trust memories in type",
                            "memory_type": row["memory_type"],
                            "count": row["cnt"],
                            "avg_trust": float(row["avg_trust"]),
                        }
                    ),
                    row["memory_type"],
                    self.WEAK_SIGNAL_MAX_TRUST,
                )
                flagged += 1
            except Exception:
                logger.warning("weak signal scan: failed to flag type %s", row["memory_type"], exc_info=True)
        return {"weak_signal_types_flagged": flagged}
