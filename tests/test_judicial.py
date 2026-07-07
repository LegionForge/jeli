"""Unit tests for the Judicial precedent system — all DB calls mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.jeli_scoped_mcp.daemons.conflict_resolver import ConflictResolverDaemon
from src.jeli_scoped_mcp.judicial.escalation import HumanEscalationQueue
from src.jeli_scoped_mcp.judicial.precedent import JudicialPrecedent, PrecedentStore

# ── helpers ────────────────────────────────────────────────────────────────────

def _precedent_row(
    *,
    id="p1",
    contradiction_type="direct",
    pattern_hash="ph",
    resolution="trust_wins",
    winner_rule="higher trust_score prevails",
    confidence=0.5,
    applied_count=1,
):
    return {
        "id": id,
        "contradiction_type": contradiction_type,
        "pattern_hash": pattern_hash,
        "resolution": resolution,
        "winner_rule": winner_rule,
        "confidence": confidence,
        "applied_count": applied_count,
        "first_set_at": None,
        "last_applied_at": None,
    }


def _db(fetchrow=None, fetchall=None, fetchval=None, execute_result="UPDATE 1"):
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value=fetchrow)
    db.fetchall = AsyncMock(return_value=fetchall or [])
    db.fetchval = AsyncMock(return_value=fetchval)
    db.execute = AsyncMock(return_value=execute_result)
    return db


def _resolver(db):
    return ConflictResolverDaemon(
        db=db, embedder=MagicMock(), chain_key="test-chain-key", worker_id="cr-test"
    )


# ── pattern_hash ─────────────────────────────────────────────────────────────────

def test_pattern_hash_is_symmetric():
    store = PrecedentStore()
    assert store.pattern_hash("direct", "preference", "identity") == store.pattern_hash(
        "direct", "identity", "preference"
    )


def test_pattern_hash_distinguishes_contradiction_type():
    store = PrecedentStore()
    assert store.pattern_hash("direct", "preference", "identity") != store.pattern_hash(
        "trust_conflict", "preference", "identity"
    )


# ── PrecedentStore lookup / record / reinforce ───────────────────────────────────

@pytest.mark.asyncio
async def test_lookup_returns_none_when_empty():
    store = PrecedentStore()
    db = _db(fetchrow=None)
    assert await store.lookup(db, "ph") is None


@pytest.mark.asyncio
async def test_record_then_lookup():
    store = PrecedentStore()
    row = _precedent_row(pattern_hash="ph-xyz")
    db = _db(fetchrow=row)

    recorded = await store.record(
        db, "ph-xyz", "direct", "trust_wins", "higher trust_score prevails"
    )
    assert isinstance(recorded, JudicialPrecedent)
    assert recorded.pattern_hash == "ph-xyz"

    found = await store.lookup(db, "ph-xyz")
    assert found is not None
    assert found.id == recorded.id


@pytest.mark.asyncio
async def test_reinforce_increments_count():
    store = PrecedentStore()
    db = _db()
    await store.reinforce(db, "p1")
    db.execute.assert_awaited_once()
    query = db.execute.await_args.args[0]
    assert "applied_count = applied_count + 1" in query


# ── conflict resolver precedent path ─────────────────────────────────────────────

def _mem(id, trust, mtype="preference"):
    return {"id": id, "trust_score": trust, "memory_type": mtype, "content": "x"}


@pytest.mark.asyncio
async def test_high_confidence_precedent_applied():
    db = _db()
    resolver = _resolver(db)
    high = JudicialPrecedent(
        id="p1",
        contradiction_type="direct",
        pattern_hash="ph",
        resolution="trust_wins",
        winner_rule="higher trust_score prevails",
        confidence=0.9,
        applied_count=5,
    )
    store = MagicMock()
    store.pattern_hash = MagicMock(return_value="ph")
    store.lookup = AsyncMock(return_value=high)
    store.reinforce = AsyncMock()
    store.record = AsyncMock()

    with patch(
        "src.jeli_scoped_mcp.judicial.precedent.PrecedentStore", return_value=store
    ), patch("src.jeli_scoped_mcp.tools.memory_tools.MemoryTools"), patch(
        "src.jeli_scoped_mcp.tools.state_tools.StateTools"
    ) as MockState:
        MockState.return_value.invalidate = AsyncMock()
        await resolver._resolve_high(
            _mem("new", 0.9, "preference"), _mem("old", 0.5, "identity"), "reason", "direct"
        )

    store.reinforce.assert_awaited_once()
    store.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_low_confidence_rediscovers():
    db = _db()
    resolver = _resolver(db)
    low = JudicialPrecedent(
        id="p1",
        contradiction_type="direct",
        pattern_hash="ph",
        resolution="trust_wins",
        winner_rule="higher trust_score prevails",
        confidence=0.5,
        applied_count=1,
    )
    store = MagicMock()
    store.pattern_hash = MagicMock(return_value="ph")
    store.lookup = AsyncMock(return_value=low)
    store.reinforce = AsyncMock()
    store.record = AsyncMock()

    with patch(
        "src.jeli_scoped_mcp.judicial.precedent.PrecedentStore", return_value=store
    ), patch("src.jeli_scoped_mcp.tools.memory_tools.MemoryTools"), patch(
        "src.jeli_scoped_mcp.tools.state_tools.StateTools"
    ) as MockState:
        MockState.return_value.invalidate = AsyncMock()
        await resolver._resolve_high(
            _mem("new", 0.9, "preference"), _mem("old", 0.5, "identity"), "reason", "direct"
        )

    store.record.assert_awaited_once()
    store.reinforce.assert_not_awaited()


# ── HumanEscalationQueue ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_escalation_enqueue():
    queue = HumanEscalationQueue()
    db = _db(fetchval="entry-123")
    entry_id = await queue.enqueue(db, "a", "b", "direct", "repeated conflict", "medium")
    assert entry_id == "entry-123"


@pytest.mark.asyncio
async def test_resolve_escalation():
    queue = HumanEscalationQueue()
    db = _db(execute_result="UPDATE 1")
    await queue.resolve(db, "entry-123", "newer_wins", "jp-cruz")
    db.execute.assert_awaited_once()
    query = db.execute.await_args.args[0]
    assert "resolved_at = now()" in query


@pytest.mark.asyncio
async def test_resolve_escalation_missing_raises():
    queue = HumanEscalationQueue()
    db = _db(execute_result="UPDATE 0")
    with pytest.raises(ValueError):
        await queue.resolve(db, "missing", "newer_wins", "jp-cruz")


# ── medium-conflict escalation wiring ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_repeated_medium_conflict_escalates():
    # fetchval returns the recent conflict_flagged count (3 → escalate).
    db = _db(fetchval=3)
    resolver = _resolver(db)

    queue = MagicMock()
    queue.enqueue = AsyncMock(return_value="entry-1")

    with patch(
        "src.jeli_scoped_mcp.judicial.escalation.HumanEscalationQueue", return_value=queue
    ):
        await resolver._log_conflict("mem-a", "mem-b", "reason", "medium", "direct")

    queue.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_single_medium_conflict_does_not_escalate():
    db = _db(fetchval=1)
    resolver = _resolver(db)

    queue = MagicMock()
    queue.enqueue = AsyncMock(return_value="entry-1")

    with patch(
        "src.jeli_scoped_mcp.judicial.escalation.HumanEscalationQueue", return_value=queue
    ):
        await resolver._log_conflict("mem-a", "mem-b", "reason", "medium", "direct")

    queue.enqueue.assert_not_awaited()
