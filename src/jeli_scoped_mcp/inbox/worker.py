"""InboxWorker — drains memory_inbox pending rows.

N instances safe: claim uses FOR UPDATE SKIP LOCKED so each row is
processed by exactly one worker regardless of how many are running.
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from ..database.pool import AsyncPostgresPool
from ..security import InjectionDefense
from ..tools.memory_tools import MemoryTools
from .classifier import IngestionClassifier
from .models import InboxStatus

logger = logging.getLogger(__name__)


class InboxWorker:
    BATCH_SIZE = 10
    POLL_INTERVAL_SECONDS = 5.0
    MAX_RETRIES = 3

    def __init__(
        self,
        db: AsyncPostgresPool,
        classifier: IngestionClassifier,
        memory_tools: MemoryTools,
        worker_id: str | None = None,
        instance_index: int = 0,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        max_retries: int = MAX_RETRIES,
        llm_model: str | None = None,
    ):
        self.db = db
        self.classifier = classifier
        self.memory_tools = memory_tools
        self.worker_id = worker_id or f"inbox-worker-{uuid.uuid4().hex[:8]}"
        self.instance_index = instance_index
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self.llm_model = llm_model

    async def run_once(self) -> int:
        """Claim and process one batch. Returns items processed count."""
        rows = await self._claim_batch()
        if not rows:
            return 0
        count = 0
        for row in rows:
            await self._process_row(dict(row))
            count += 1
        return count

    async def run_forever(self):
        """Poll loop — runs until cancelled."""
        logger.info("inbox worker %s (index=%d) started", self.worker_id, self.instance_index)
        while True:
            try:
                processed = await self.run_once()
                if processed == 0:
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                logger.info("inbox worker %s cancelled", self.worker_id)
                return
            except Exception:
                logger.exception("inbox worker %s error in poll loop", self.worker_id)
                await asyncio.sleep(self.poll_interval)

    async def _claim_batch(self) -> list:
        if not self.db.pool:
            return []
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    UPDATE memory_inbox SET status = 'processing', worker_id = $1
                    WHERE id IN (
                        SELECT id FROM memory_inbox
                        WHERE status = 'pending'
                        ORDER BY submitted_at ASC
                        LIMIT $2
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING *
                    """,
                    self.worker_id,
                    self.BATCH_SIZE,
                )
        return list(rows)

    async def _process_row(self, row: dict) -> None:
        inbox_id = str(row["id"])
        content = row["content"]
        caller_type = row["caller_type"]
        caller_trust = float(row["caller_trust"])
        source_agent = row["source_agent"]
        session_id = row.get("session_id")
        retry_count = row.get("retry_count", 0)
        content_class: str = row.get("content_class") or "general"
        source_metadata: dict = {}
        if row.get("source_metadata"):
            raw = row["source_metadata"]
            source_metadata = json.loads(raw) if isinstance(raw, str) else dict(raw)

        try:
            decision = await self.classifier.classify(
                content=content,
                caller_type=caller_type,
                caller_trust=caller_trust,
                source_agent=source_agent,
            )

            # Bouncer second pass: LLM injection classifier on items headed for
            # the chain. Catches natural-language injection the classifier's
            # heuristics miss (GH #33). Runs only when a model is configured, and
            # only on would-be writes. Fail-open — a classifier outage must never
            # block or hold a legitimate item.
            review_reason = decision.review_reason
            requires_review = decision.requires_review
            llm_held = False
            if self.llm_model and decision.status in (
                InboxStatus.APPROVED,
                InboxStatus.MERGED,
            ):
                try:
                    _, llm_flagged, _ = await InjectionDefense.sanitize_content_async(
                        content,
                        source_trust=caller_trust,
                        content_class=content_class,
                        llm_model=self.llm_model,
                    )
                    if llm_flagged:
                        llm_held = True
                        review_reason = "llm_classifier"
                        requires_review = True
                        logger.warning(
                            "inbox %s: llm classifier flagged injection — holding", inbox_id
                        )
                except Exception:
                    logger.warning(
                        "inbox %s: llm injection classifier failed open", inbox_id, exc_info=True
                    )

            promoted_to: str | None = None

            if llm_held:
                final_status = "held"
            elif decision.status in (InboxStatus.APPROVED, InboxStatus.MERGED):
                amended_from = decision.near_duplicate_of if decision.status == InboxStatus.MERGED else None
                result = await self.memory_tools.capture_memory(
                    content=content,
                    memory_type=decision.suggested_type or caller_type,
                    trust_score=decision.suggested_trust,
                    actor=source_agent,
                    source_agent=source_agent,
                    session_id=session_id,
                    content_class=content_class,
                    metadata={
                        "inbox_id": inbox_id,
                        "importance": decision.importance,
                        "urgency": decision.urgency.value,
                        "durability": decision.durability.value,
                        "entities": decision.entities,
                        "keywords": decision.keywords,
                        # Preserve original caller metadata (source_path etc.)
                        **source_metadata,
                        **({"amended_from": amended_from} if amended_from else {}),
                    },
                )
                promoted_to = result.get("id")
                final_status = decision.status.value

            elif decision.status == InboxStatus.REJECTED:
                final_status = "rejected"
            else:
                final_status = "held"

            await self.db.execute(
                """
                UPDATE memory_inbox SET
                    status = $1,
                    importance = $2,
                    urgency = $3,
                    durability = $4,
                    encoding = $5,
                    suggested_type = $6,
                    suggested_trust = $7,
                    keywords = $8,
                    entities = $9::jsonb,
                    near_duplicate_of = $10,
                    duplicate_distance = $11,
                    merge_strategy = $12,
                    requires_review = $13,
                    review_reason = $14,
                    rejection_reason = $15,
                    classifier_version = $16,
                    processed_at = $17,
                    promoted_to = $18,
                    enrichment_log = $19::jsonb,
                    error = NULL
                WHERE id = $20
                """,
                final_status,
                decision.importance,
                decision.urgency.value,
                decision.durability.value,
                decision.encoding.value,
                decision.suggested_type,
                decision.suggested_trust,
                decision.keywords or [],
                json.dumps(decision.entities),
                decision.near_duplicate_of,
                decision.duplicate_distance,
                decision.merge_strategy,
                requires_review,
                review_reason,
                decision.rejection_reason,
                self.classifier.CLASSIFIER_VERSION,
                datetime.now(UTC),
                promoted_to,
                json.dumps(decision.enrichment_log),
                inbox_id,
            )
            logger.info(
                "inbox %s: status=%s promoted_to=%s", inbox_id, final_status, promoted_to
            )

        except Exception as exc:
            logger.exception("inbox %s: processing failed (retry %d)", inbox_id, retry_count)
            new_retry = retry_count + 1
            new_status = "held" if new_retry >= self.max_retries else "pending"
            await self.db.execute(
                """
                UPDATE memory_inbox SET
                    status = $1, retry_count = $2, error = $3, worker_id = NULL
                WHERE id = $4
                """,
                new_status,
                new_retry,
                str(exc)[:500],
                inbox_id,
            )
