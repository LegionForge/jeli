"""Unit tests for IngestionClassifier heuristics — no DB or embedder required."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.jeli_scoped_mcp.inbox.classifier import IngestionClassifier, content_hash
from src.jeli_scoped_mcp.inbox.models import Durability, InboxStatus, Urgency


def _make_classifier(nearest_distance: float | None = None) -> IngestionClassifier:
    """Build a classifier with a mocked embedder and DB."""
    embedder = MagicMock()
    embedding_result = MagicMock()
    embedding_result.vector = [0.1] * 1024
    embedder.embed = AsyncMock(return_value=embedding_result)

    db = MagicMock()
    if nearest_distance is None:
        db.fetchrow = AsyncMock(return_value=None)
    else:
        row = MagicMock()
        row.__getitem__ = lambda self, key: {
            "id": "aaaaaaaa-0000-0000-0000-000000000001",
            "dist": nearest_distance,
        }[key]
        db.fetchrow = AsyncMock(return_value=row)

    return IngestionClassifier(embedder=embedder, db=db)


# ── durability ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_durability_transient_pattern():
    c = _make_classifier()
    d = await c.classify("I am currently working on the deployment", "episodic", 0.6, "agent")
    assert d.durability == Durability.TRANSIENT


@pytest.mark.asyncio
async def test_durability_permanent_pattern():
    c = _make_classifier()
    d = await c.classify("I prefer directness over hand-holding", "preference", 1.0, "user")
    assert d.durability == Durability.PERMANENT


@pytest.mark.asyncio
async def test_durability_identity_type_forces_permanent():
    c = _make_classifier()
    d = await c.classify("JP Cruz �� software developer", "identity", 1.0, "user")
    assert d.durability == Durability.PERMANENT


@pytest.mark.asyncio
async def test_durability_default_durable():
    c = _make_classifier()
    d = await c.classify("The database runs on port 5442", "semantic", 0.6, "agent")
    assert d.durability == Durability.DURABLE


# ── importance / urgency ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_importance_identity_high_trust():
    c = _make_classifier()
    d = await c.classify("My name is JP Cruz", "identity", 1.0, "user")
    assert d.importance > 0.8


@pytest.mark.asyncio
async def test_importance_transient_low():
    c = _make_classifier()
    d = await c.classify("remind me to check email", "transient", 0.3, "agent")
    assert d.importance < 0.5


@pytest.mark.asyncio
async def test_urgency_transient_max_importance_is_below_threshold():
    c = _make_classifier()
    # transient type_weight=0.2 caps max importance at 0.68 (trust=1.0, length=500+)
    # so urgency is always LOW for transient content regardless of trust
    d = await c.classify("remind me: critical production deploy happening NOW", "transient", 1.0, "user")
    assert d.urgency == Urgency.LOW


@pytest.mark.asyncio
async def test_urgency_transient_low_importance_is_low():
    c = _make_classifier()
    d = await c.classify("thinking about lunch", "transient", 0.3, "agent")
    assert d.urgency == Urgency.LOW


# ── type correction ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_type_corrected_episodic_to_preference():
    c = _make_classifier()
    d = await c.classify("I really like dark mode interfaces", "episodic", 0.6, "agent")
    assert d.suggested_type == "preference"
    assert "type_corrected" in d.enrichment_log


@pytest.mark.asyncio
async def test_type_corrected_semantic_to_identity():
    c = _make_classifier()
    d = await c.classify("I'm a NetSuite developer by trade", "semantic", 0.6, "agent")
    assert d.suggested_type == "identity"


@pytest.mark.asyncio
async def test_type_unchanged_when_no_signal():
    c = _make_classifier()
    d = await c.classify("The alembic migration runs on startup", "procedural", 0.6, "agent")
    assert d.suggested_type == "procedural"


# ── trust calibration ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trust_capped_for_transient_high_claim():
    c = _make_classifier()
    d = await c.classify("currently in a meeting", "transient", 1.0, "user")
    assert d.suggested_trust <= 0.7
    assert "trust_capped" in d.enrichment_log


@pytest.mark.asyncio
async def test_trust_unchanged_for_durable():
    c = _make_classifier()
    d = await c.classify("I prefer tabs over spaces", "preference", 1.0, "user")
    assert d.suggested_trust == 1.0


# ── dedup ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dedup_reject_below_threshold():
    c = _make_classifier(nearest_distance=0.05)
    d = await c.classify("some content", "semantic", 0.6, "agent")
    assert d.status == InboxStatus.REJECTED
    assert d.rejection_reason == "exact duplicate"


@pytest.mark.asyncio
async def test_dedup_merge_in_range():
    c = _make_classifier(nearest_distance=0.12)
    d = await c.classify("some content", "semantic", 0.6, "agent")
    assert d.status == InboxStatus.MERGED
    assert d.merge_strategy == "append"
    assert d.near_duplicate_of is not None


@pytest.mark.asyncio
async def test_dedup_hold_for_review():
    c = _make_classifier(nearest_distance=0.18)
    d = await c.classify("some content", "semantic", 0.6, "agent")
    assert d.requires_review is True
    assert d.review_reason is not None


@pytest.mark.asyncio
async def test_dedup_approve_when_no_neighbor():
    c = _make_classifier(nearest_distance=None)
    d = await c.classify("totally unique fact about the universe", "semantic", 0.6, "agent")
    assert d.status == InboxStatus.APPROVED


# ── transient rejection ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transient_low_importance_rejected():
    c = _make_classifier(nearest_distance=None)
    d = await c.classify("thinking about lunch options", "transient", 0.3, "agent")
    assert d.status == InboxStatus.REJECTED
    assert d.rejection_reason == "transient low-importance content"


# ── keyword / entity extraction ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_keywords_extracted():
    c = _make_classifier()
    d = await c.classify(
        "python asyncio postgres pgvector semantic search embeddings", "semantic", 0.6, "agent"
    )
    assert len(d.keywords) > 0
    assert any(k in d.keywords for k in ("python", "asyncio", "postgres", "semantic", "search", "embeddings"))


@pytest.mark.asyncio
async def test_entities_tools_extracted():
    c = _make_classifier()
    d = await c.classify("Using docker and postgres with ollama for embeddings", "semantic", 0.6, "agent")
    assert "docker" in d.entities.get("tools", [])
    assert "postgres" in d.entities.get("tools", []) or "postgresql" in d.entities.get("tools", [])


# ── content_hash ───────────────────────────────────────────────────────────────

def test_content_hash_normalizes_whitespace():
    h1 = content_hash("Hello   World")
    h2 = content_hash("hello world")
    assert h1 == h2


def test_content_hash_different_content():
    assert content_hash("foo") != content_hash("bar")
