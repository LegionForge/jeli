"""DaemonRunner — supervises inbox workers + conflict resolver with per-task restart.

Each task restarts independently on crash with exponential backoff.
Insights and maintenance are one-shot; runner exposes manual triggers for them.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from ..config import Settings
from ..database.pool import AsyncPostgresPool
from ..embedding.provider import EmbeddingProvider
from ..inbox.classifier import IngestionClassifier
from ..inbox.worker import InboxWorker
from ..tools.memory_tools import MemoryTools
from .conflict_resolver import ConflictResolverDaemon
from .insights import InsightsDaemon
from .maintenance import MaintenanceDaemon

logger = logging.getLogger(__name__)

_MAX_BACKOFF = 60.0
_BASE_BACKOFF = 2.0


async def _supervised(coro_factory, name: str):
    """Run a coroutine factory in a restart loop with exponential backoff."""
    backoff = _BASE_BACKOFF
    while True:
        try:
            await coro_factory()
            backoff = _BASE_BACKOFF
        except asyncio.CancelledError:
            logger.info("%s cancelled", name)
            return
        except Exception:
            logger.exception("%s crashed — restarting in %.1fs", name, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)


class DaemonRunner:
    def __init__(
        self,
        db: AsyncPostgresPool,
        embedder: EmbeddingProvider,
        memory_tools: MemoryTools,
        settings: Settings,
        runner_id: str | None = None,
    ):
        self.db = db
        self.embedder = embedder
        self.memory_tools = memory_tools
        self.settings = settings
        self.runner_id = runner_id or f"runner-{uuid.uuid4().hex[:8]}"
        self._tasks: list[asyncio.Task] = []

    async def _check_embedder(self) -> None:
        """Fail fast if the embedding service is unreachable at startup."""
        try:
            await self.embedder.embed("health check")
        except Exception as exc:
            provider = type(self.embedder).__name__
            raise RuntimeError(
                f"Embedding service unreachable ({provider}): {exc}\n"
                "Start Ollama and ensure the model is loaded before running daemons."
            ) from exc

    async def run_forever(self):
        """Start all daemons. Each restarts independently on crash."""
        await self._check_embedder()
        classifier = IngestionClassifier(
            embedder=self.embedder,
            db=self.db,
            dedup_reject=self.settings.inbox_dedup_reject_distance,
            dedup_merge=self.settings.inbox_dedup_merge_distance,
            dedup_hold=self.settings.inbox_dedup_hold_distance,
            litellm_base_url=self.settings.litellm_base_url,
            litellm_api_key=self.settings.litellm_api_key,
            llm_model=self.settings.reranker_model,
        )
        concurrency = self.settings.inbox_worker_concurrency

        for i in range(concurrency):
            worker_id = f"{self.runner_id}-inbox-{i}"
            worker = InboxWorker(
                db=self.db,
                classifier=classifier,
                memory_tools=self.memory_tools,
                worker_id=worker_id,
                instance_index=i,
                poll_interval=self.settings.inbox_poll_interval,
                max_retries=self.settings.inbox_max_retries,
                llm_model=(
                    self.settings.reranker_model if self.settings.litellm_base_url else None
                ),
                llm_api_base=self.settings.litellm_base_url or None,
                llm_api_key=self.settings.litellm_api_key,
            )
            self._tasks.append(
                asyncio.create_task(
                    _supervised(worker.run_forever, f"inbox-worker-{i}"),
                    name=f"inbox-worker-{i}",
                )
            )

        resolver_concurrency = self.settings.conflict_resolver_concurrency
        for i in range(resolver_concurrency):
            resolver_id = f"{self.runner_id}-resolver-{i}"
            resolver = ConflictResolverDaemon(
                db=self.db,
                embedder=self.embedder,
                chain_key=self.settings.chain_key,
                key_id=self.settings.chain_key_id,
                worker_id=resolver_id,
                instance_index=i,
            )
            self._tasks.append(
                asyncio.create_task(
                    _supervised(resolver.run_forever, f"conflict-resolver-{i}"),
                    name=f"conflict-resolver-{i}",
                )
            )

        logger.info(
            "daemon runner %s: started %d inbox workers + %d conflict resolvers",
            self.runner_id,
            concurrency,
            resolver_concurrency,
        )
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            for t in self._tasks:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            logger.info("daemon runner %s: all tasks stopped", self.runner_id)

    async def run_insights_once(self) -> dict:
        run_id = await self._start_run("insights")
        daemon = InsightsDaemon(
            db=self.db,
            embedder=self.embedder,
            memory_tools=self.memory_tools,
        )
        try:
            result = await daemon.run_once()
            await self._finish_run(run_id, "completed", sum(v for v in result.values() if isinstance(v, int)))
            return result
        except Exception as exc:
            await self._finish_run(run_id, "failed", error=str(exc))
            raise

    async def run_maintenance_once(self) -> dict:
        run_id = await self._start_run("maintenance")
        daemon = MaintenanceDaemon(db=self.db, memory_tools=self.memory_tools)
        try:
            result = await daemon.run_once()
            total = sum(v for v in result.values() if isinstance(v, int))
            await self._finish_run(run_id, "completed", total)
            return result
        except Exception as exc:
            await self._finish_run(run_id, "failed", error=str(exc))
            raise

    async def _start_run(self, name: str) -> str:
        row = await self.db.fetchrow(
            """
            INSERT INTO daemon_runs (daemon_name, worker_id, status)
            VALUES ($1, $2, 'running')
            RETURNING id
            """,
            name,
            self.runner_id,
        )
        return str(row["id"]) if row else ""

    async def _finish_run(
        self, run_id: str, status: str, items: int = 0, error: str | None = None
    ) -> None:
        if not run_id:
            return
        await self.db.execute(
            """
            UPDATE daemon_runs
            SET status = $1, finished_at = $2, items_processed = $3, error = $4
            WHERE id = $5
            """,
            status,
            datetime.now(UTC),
            items,
            error,
            run_id,
        )
