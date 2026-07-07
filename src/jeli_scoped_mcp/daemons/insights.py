"""InsightsDaemon — nightly cluster scan, stale procedural detection, weak signal flagging."""

import json
import logging
import re
from datetime import UTC, datetime, timedelta

from ..config import Settings
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
    STUCK_CONFLICT_HOURS = 24
    SYNTHESIS_TIMEOUT = 30.0
    MEMBER_SYNTHESIS_LIMIT = 10

    def __init__(
        self,
        db: AsyncPostgresPool,
        embedder: EmbeddingProvider,
        memory_tools: MemoryTools,
        actor: str = "insights-daemon",
        settings: Settings | None = None,
    ):
        self.db = db
        self.embedder = embedder
        self.memory_tools = memory_tools
        self.actor = actor
        self.settings = settings or Settings()

    async def run_once(self) -> dict:
        results: dict = {}
        results["clusters"] = await self._cluster_scan()
        results["stale_procedural"] = await self._stale_procedural_scan()
        results["weak_signal"] = await self._weak_signal_scan()
        results["contradictions"] = await self._contradiction_surfacing()
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
            return {"clusters_found": 0, "synthesis_used": False}

        visited: set[str] = set()
        clusters_written = 0
        synthesis_used = False

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

            # Synthesize members into an insight via LLM; fall back to a
            # member-snippet list when no LLM is configured or the call fails.
            member_contents = [row["content"]] + [n["content"] for n in neighbors[:9]]
            summary = await self._synthesize_cluster(member_contents)
            if summary.startswith("Insight:"):
                synthesis_used = True

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

        return {"clusters_found": clusters_written, "synthesis_used": synthesis_used}

    async def _synthesize_cluster(self, member_contents: list[str]) -> str:
        """Use an LLM to synthesize cluster members into a meaningful insight.

        Returns "Insight: {text}" on success. Falls back to a "Cluster: ..."
        member-snippet list if no LLM is configured or the call fails — the
        daemon must never crash on LLM unavailability.
        """
        fallback = "Cluster: " + " | ".join(c[:50] for c in member_contents[:5])

        if not self.settings.litellm_base_url:
            return fallback

        snippets = "\n".join(
            f"- {c[:200]}" for c in member_contents[: self.MEMBER_SYNTHESIS_LIMIT]
        )
        prompt = (
            f"You are analyzing a cluster of related memories. Synthesize these "
            f"{len(member_contents)} related memories into a concise insight (1-2 "
            f"sentences) that captures the key theme or pattern:\n\n{snippets}\n\n"
            "Respond with only the synthesized insight. No preamble, no explanation."
        )
        try:
            text = await self._call_synthesis_llm(prompt)
        except Exception:
            logger.warning("cluster synthesis: LLM call failed", exc_info=True)
            return fallback

        if not text:
            return fallback
        return f"Insight: {text.strip()}"

    async def _call_synthesis_llm(self, prompt: str) -> str | None:
        """POST the prompt to the configured LiteLLM proxy; return the reply text."""
        import aiohttp

        url = f"{self.settings.litellm_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.settings.reranker_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 200,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.litellm_api_key}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.SYNTHESIS_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        text = data["choices"][0]["message"]["content"]
        # Collapse whitespace so multi-line replies stay a single-line insight.
        return re.sub(r"\s+", " ", text).strip()

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

    async def _contradiction_surfacing(self) -> dict:
        """Surface conflict-queue entries stuck in 'failed' for >24h to the user.

        The conflict resolver could not adjudicate these automatically; they need
        human review. We log a 'contradiction_surfacing_needed' audit action once
        per stuck memory (idempotent via NOT EXISTS)."""
        cutoff = datetime.now(UTC) - timedelta(hours=self.STUCK_CONFLICT_HOURS)
        rows = await self.db.fetchall(
            """
            SELECT cq.id, cq.memory_id, cq.error
            FROM memory_conflict_queue cq
            WHERE cq.status = 'failed'
              AND COALESCE(cq.finished_at, cq.claimed_at, cq.enqueued_at) < $1
              AND NOT EXISTS (
                  SELECT 1 FROM memory_audit_log mal
                  WHERE mal.memory_id = cq.memory_id
                    AND mal.action = 'contradiction_surfacing_needed'
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
                    VALUES ($1, 'contradiction_surfacing_needed', $2, $3::jsonb)
                    """,
                    row["memory_id"],
                    self.actor,
                    json.dumps(
                        {
                            "reason": f"conflict resolution stuck in 'failed' >"
                            f"{self.STUCK_CONFLICT_HOURS}h",
                            "conflict_queue_id": str(row["id"]),
                            "error": row["error"],
                        }
                    ),
                )
                flagged += 1
            except Exception:
                logger.warning(
                    "contradiction surfacing: failed to flag %s", row["memory_id"], exc_info=True
                )
        return {"stuck_conflicts_flagged": flagged}
