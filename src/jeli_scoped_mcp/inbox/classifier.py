"""IngestionClassifier — the Bouncer.

Heuristic-only v1: no LLM calls. Pure regex + formula + embedding dedup.
Keeps write-path latency predictable and avoids circular LLM-classifying-LLM-output.
"""

import hashlib
import json
import logging
import re
from typing import Optional

from ..database.pool import AsyncPostgresPool
from ..embedding.provider import EmbeddingProvider
from .models import ClassifierDecision, Durability, Encoding, InboxStatus, Urgency

logger = logging.getLogger(__name__)

_TRANSIENT_RE = re.compile(
    r"\b(currently|right now|today i|working on|remind me|don't forget|"
    r"will be|going to|planning to|thinking about|notes from session|in progress)\b",
    re.IGNORECASE,
)
_PERMANENT_RE = re.compile(
    r"\b(always|never|i prefer|i like|i dislike|i hate|i am|i'm a|"
    r"my name|i work as|i specialize)\b",
    re.IGNORECASE,
)
_PREFERENCE_RE = re.compile(
    r"\b(prefer|like|love|enjoy|hate|dislike|don't like|avoid)\b", re.IGNORECASE
)
_FIRST_PERSON_RE = re.compile(r"\b(i am|i'm|my|i have|i've|i do|i don't)\b", re.IGNORECASE)

_TYPE_WEIGHTS: dict[str, float] = {
    "identity": 1.0,
    "preference": 0.9,
    "procedural": 0.8,
    "semantic": 0.7,
    "episodic": 0.5,
    "transient": 0.2,
}

_STOPWORDS = frozenset(
    "the a an and or but in on at to for of with is are was were be been "
    "have has had do does did will would could should may might shall can "
    "this that these those it its i me my we our you your he she they them "
    "his her their what which who how when where why not no yes just also "
    "very more some any all get got from by up out if so than then".split()
)

_TECH_TOOLS = frozenset(
    "python postgres postgresql pgvector docker redis kafka ollama openai anthropic "
    "claude hermes discord slack github jeli ob1 mcp asyncpg alembic fastapi "
    "pydantic typescript javascript react nextjs".split()
)


class IngestionClassifier:
    CLASSIFIER_VERSION = "1.0.0-heuristic"
    DEDUP_REJECT_DISTANCE = 0.10
    DEDUP_MERGE_DISTANCE = 0.15
    DEDUP_HOLD_DISTANCE = 0.22

    def __init__(
        self,
        embedder: EmbeddingProvider,
        db: AsyncPostgresPool,
        dedup_reject: float = DEDUP_REJECT_DISTANCE,
        dedup_merge: float = DEDUP_MERGE_DISTANCE,
        dedup_hold: float = DEDUP_HOLD_DISTANCE,
    ):
        self.embedder = embedder
        self.db = db
        self.dedup_reject = dedup_reject
        self.dedup_merge = dedup_merge
        self.dedup_hold = dedup_hold

    async def classify(
        self,
        content: str,
        caller_type: str,
        caller_trust: float,
        source_agent: str,
    ) -> ClassifierDecision:
        log: dict = {}

        durability = self._detect_durability(content, caller_type, log)
        importance = self._score_importance(content, caller_type, caller_trust)
        urgency = self._score_urgency(durability, importance)
        suggested_type = self._correct_type(content, caller_type, log)
        suggested_trust = self._calibrate_trust(caller_trust, durability, log)
        keywords = self._extract_keywords(content)
        entities = self._extract_entities(content)
        encoding = Encoding.HYBRID if len(content) > 2000 else Encoding.RAW

        # Semantic dedup — the only async step.
        dup_id, dup_dist, dup_strategy, requires_review, review_reason = (
            await self._check_dedup(content)
        )

        # Routing decision.
        if dup_dist is not None and dup_dist < self.dedup_reject:
            status = InboxStatus.REJECTED
            rejection_reason: Optional[str] = "exact duplicate"
        elif dup_dist is not None and dup_dist < self.dedup_merge:
            status = InboxStatus.MERGED
            rejection_reason = None
        elif durability == Durability.TRANSIENT and importance < 0.35:
            status = InboxStatus.REJECTED
            rejection_reason = "transient low-importance content"
        else:
            status = InboxStatus.APPROVED
            rejection_reason = None

        return ClassifierDecision(
            status=status,
            importance=round(importance, 2),
            urgency=urgency,
            durability=durability,
            encoding=encoding,
            suggested_type=suggested_type,
            suggested_trust=round(suggested_trust, 2),
            keywords=keywords,
            entities=entities,
            requires_review=requires_review,
            review_reason=review_reason,
            near_duplicate_of=dup_id,
            duplicate_distance=round(dup_dist, 4) if dup_dist is not None else None,
            merge_strategy=dup_strategy,
            rejection_reason=rejection_reason,
            enrichment_log=log,
        )

    # ── heuristics ─────────────────────────────────────────────────────────────

    def _detect_durability(self, content: str, caller_type: str, log: dict) -> Durability:
        if caller_type == "transient":
            return Durability.TRANSIENT
        if caller_type == "identity":
            return Durability.PERMANENT
        if _TRANSIENT_RE.search(content):
            log["durability_signal"] = "transient_pattern"
            return Durability.TRANSIENT
        if _PERMANENT_RE.search(content):
            log["durability_signal"] = "permanent_pattern"
            return Durability.PERMANENT
        return Durability.DURABLE

    def _score_importance(self, content: str, caller_type: str, caller_trust: float) -> float:
        type_weight = _TYPE_WEIGHTS.get(caller_type, 0.5)
        length_score = min(1.0, len(content) / 500)
        return 0.40 * caller_trust + 0.40 * type_weight + 0.20 * length_score

    def _score_urgency(self, durability: Durability, importance: float) -> Urgency:
        if durability == Durability.TRANSIENT:
            return Urgency.HIGH if importance > 0.7 else Urgency.LOW
        if durability == Durability.PERMANENT and importance > 0.8:
            return Urgency.HIGH
        if importance > 0.85:
            return Urgency.HIGH
        return Urgency.MEDIUM

    def _correct_type(self, content: str, caller_type: str, log: dict) -> str:
        if caller_type == "episodic" and _PREFERENCE_RE.search(content):
            log["type_corrected"] = f"episodic -> preference (preference pattern)"
            return "preference"
        if caller_type == "semantic" and _FIRST_PERSON_RE.search(content):
            log["type_corrected"] = f"semantic -> identity (first-person pattern)"
            return "identity"
        return caller_type

    def _calibrate_trust(self, caller_trust: float, durability: Durability, log: dict) -> float:
        if durability == Durability.TRANSIENT and caller_trust >= 0.9:
            log["trust_capped"] = f"{caller_trust} -> 0.7 (transient)"
            return 0.7
        return caller_trust

    def _extract_keywords(self, content: str) -> list[str]:
        words = re.findall(r"\b[a-zA-Z]{5,}\b", content.lower())
        freq: dict[str, int] = {}
        for w in words:
            if w not in _STOPWORDS:
                freq[w] = freq.get(w, 0) + 1
        return sorted(freq, key=lambda k: -freq[k])[:20]

    def _extract_entities(self, content: str) -> dict:
        people = list(
            dict.fromkeys(re.findall(r"@(\w+)|(?<![A-Z])([A-Z][a-z]+\s[A-Z][a-z]+)", content))
        )
        # Flatten tuple matches from alternation.
        people_flat = []
        for p in people:
            if isinstance(p, tuple):
                people_flat.extend(x for x in p if x)
            else:
                people_flat.append(p)

        tools = [w for w in re.findall(r"\b\w+\b", content.lower()) if w in _TECH_TOOLS]

        projects = list(
            dict.fromkeys(re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", content))
        )

        return {
            "people": list(dict.fromkeys(people_flat))[:10],
            "tools": list(dict.fromkeys(tools))[:10],
            "projects": projects[:10],
        }

    async def _check_dedup(
        self, content: str
    ) -> tuple[Optional[str], Optional[float], Optional[str], bool, Optional[str]]:
        """Return (dup_id, distance, merge_strategy, requires_review, review_reason)."""
        try:
            embedding = await self.embedder.embed(content)
            row = await self.db.fetchrow(
                """
                SELECT id, (embedding <=> $1::vector) AS dist
                FROM memory_entry
                WHERE valid_until IS NULL
                ORDER BY embedding <=> $1::vector
                LIMIT 1
                """,
                json.dumps(embedding.vector),
            )
        except Exception:
            logger.warning("dedup check failed — skipping", exc_info=True)
            return None, None, None, False, None

        if row is None:
            return None, None, None, False, None

        dist = float(row["dist"])
        dup_id = str(row["id"])

        if dist < self.dedup_reject:
            return dup_id, dist, None, False, None
        if dist < self.dedup_merge:
            return dup_id, dist, "append", False, None
        if dist < self.dedup_hold:
            return dup_id, dist, None, True, f"near-duplicate of {dup_id} (distance={dist:.3f})"
        return None, dist, None, False, None


def content_hash(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content.lower().strip())
    return hashlib.sha256(normalized.encode()).hexdigest()
