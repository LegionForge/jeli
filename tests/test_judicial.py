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


@pytest.mark.asyncio
async def test_list_precedents_returns_all_rows():
    row1 = _precedent_row(pattern_hash="ph1", confidence=0.9, applied_count=5)
    row2 = _precedent_row(pattern_hash="ph2", confidence=0.7, applied_count=2)
    db = _db(fetchall=[row1, row2])
    store = PrecedentStore()
    results = await store.list_precedents(db)
    assert len(results) == 2
    assert all(isinstance(r, JudicialPrecedent) for r in results)
    phs = {r.pattern_hash for r in results}
    assert {"ph1", "ph2"} == phs


@pytest.mark.asyncio
async def test_list_precedents_empty():
    db = _db(fetchall=[])
    store = PrecedentStore()
    results = await store.list_precedents(db)
    assert results == []


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


# ── user-tier tie guard (GH #37) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_tier_tie_escalates_instead_of_invalidating():
    """A recency tie between two user-tier (>=0.9) memories must not auto-
    invalidate one; it escalates to the human queue."""
    db = _db(fetchval="queue-1")
    resolver = _resolver(db)
    store = MagicMock()
    store.pattern_hash = MagicMock(return_value="ph")
    store.lookup = AsyncMock(return_value=None)
    store.record = AsyncMock()

    with patch(
        "src.jeli_scoped_mcp.judicial.precedent.PrecedentStore", return_value=store
    ), patch("src.jeli_scoped_mcp.tools.state_tools.StateTools") as MockState:
        MockState.return_value.invalidate = AsyncMock()
        await resolver._resolve_high(
            _mem("new", 1.0, "identity"), _mem("old", 1.0, "identity"), "reason", "direct"
        )
        # No invalidation happened; the conflict was enqueued for the user.
        MockState.return_value.invalidate.assert_not_awaited()
    db.fetchval.assert_awaited()  # enqueue INSERT ... RETURNING id


@pytest.mark.asyncio
async def test_low_tier_tie_still_auto_resolves():
    """A tie below user-tier resolves automatically (newer_wins), no escalation."""
    db = _db()
    resolver = _resolver(db)
    store = MagicMock()
    store.pattern_hash = MagicMock(return_value="ph")
    store.lookup = AsyncMock(return_value=None)
    store.record = AsyncMock()

    with patch(
        "src.jeli_scoped_mcp.judicial.precedent.PrecedentStore", return_value=store
    ), patch("src.jeli_scoped_mcp.tools.memory_tools.MemoryTools"), patch(
        "src.jeli_scoped_mcp.tools.state_tools.StateTools"
    ) as MockState:
        MockState.return_value.invalidate = AsyncMock()
        await resolver._resolve_high(
            _mem("new", 0.6, "preference"), _mem("old", 0.6, "preference"), "reason", "direct"
        )
        MockState.return_value.invalidate.assert_awaited_once()


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


# ── _resolve_high — new memory loses on trust ─────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_high_new_loses_when_lower_trust():
    """When old trust > new trust, the new memory is the loser and is invalidated."""
    db = _db()
    resolver = _resolver(db)
    store = MagicMock()
    store.pattern_hash = MagicMock(return_value="ph")
    store.lookup = AsyncMock(return_value=None)
    store.record = AsyncMock()

    invalidated = []

    with patch(
        "src.jeli_scoped_mcp.judicial.precedent.PrecedentStore", return_value=store
    ), patch("src.jeli_scoped_mcp.tools.memory_tools.MemoryTools"), patch(
        "src.jeli_scoped_mcp.tools.state_tools.StateTools"
    ) as MockState:
        MockState.return_value.invalidate = AsyncMock(
            side_effect=lambda memory_id, **_: invalidated.append(memory_id)
        )
        # old_trust (0.9) > new_trust (0.4) → new memory ("new-id") is the loser
        await resolver._resolve_high(
            _mem("new-id", 0.4, "preference"),
            _mem("old-id", 0.9, "preference"),
            "trust conflict",
            "direct",
        )

    assert "new-id" in invalidated


@pytest.mark.asyncio
async def test_resolve_high_equal_trust_older_loses():
    """On trust tie, the older memory wins so new-id becomes the loser too
    (new_trust >= old_trust branch — loser_id = old_mem['id'])."""
    db = _db()
    resolver = _resolver(db)
    store = MagicMock()
    store.pattern_hash = MagicMock(return_value="ph")
    store.lookup = AsyncMock(return_value=None)
    store.record = AsyncMock()

    invalidated = []

    with patch(
        "src.jeli_scoped_mcp.judicial.precedent.PrecedentStore", return_value=store
    ), patch("src.jeli_scoped_mcp.tools.memory_tools.MemoryTools"), patch(
        "src.jeli_scoped_mcp.tools.state_tools.StateTools"
    ) as MockState:
        MockState.return_value.invalidate = AsyncMock(
            side_effect=lambda memory_id, **_: invalidated.append(memory_id)
        )
        # equal trust → newer wins → old_mem is loser
        await resolver._resolve_high(
            _mem("new-id", 0.6, "preference"),
            _mem("old-id", 0.6, "preference"),
            "equal trust conflict",
            "direct",
        )

    assert "old-id" in invalidated


# ── _handle_queue_row — success and retry paths ───────────────────────────────


@pytest.mark.asyncio
async def test_handle_queue_row_success():
    """Happy path: _check_memory returns flags_found, row is marked done."""
    db = _db()
    resolver = _resolver(db)

    row = {
        "id": "q1",
        "memory_id": "m1",
        "retry_count": 0,
    }

    with patch.object(resolver, "_check_memory", AsyncMock(return_value=2)):
        await resolver._handle_queue_row(row)

    db.execute.assert_awaited_once()
    update_args = db.execute.await_args.args
    assert "status = 'done'" in update_args[0]
    assert update_args[2] == 2  # flags_found = 2


@pytest.mark.asyncio
async def test_handle_queue_row_error_retries():
    """On exception the row is re-queued as 'pending' on first retry."""
    db = _db()
    resolver = _resolver(db)

    row = {"id": "q2", "memory_id": "m2", "retry_count": 0}

    with patch.object(resolver, "_check_memory", AsyncMock(side_effect=RuntimeError("boom"))):
        await resolver._handle_queue_row(row)

    db.execute.assert_awaited_once()
    update_args = db.execute.await_args.args
    assert update_args[1] == "pending"  # status → pending (retry 1 of 3)


@pytest.mark.asyncio
async def test_handle_queue_row_error_fails_after_three():
    """After 3 retries the row is permanently marked 'failed'."""
    db = _db()
    resolver = _resolver(db)

    row = {"id": "q3", "memory_id": "m3", "retry_count": 2}

    with patch.object(resolver, "_check_memory", AsyncMock(side_effect=RuntimeError("boom"))):
        await resolver._handle_queue_row(row)

    update_args = db.execute.await_args.args
    assert update_args[1] == "failed"  # retry_count reached 3 → failed


# ── _drain_queue — returns 0 when queue is empty ─────────────────────────────


@pytest.mark.asyncio
async def test_drain_queue_empty_returns_zero():
    db = _db()
    resolver = _resolver(db)

    with patch.object(resolver, "_claim_one", AsyncMock(return_value=None)):
        result = await resolver._drain_queue()

    assert result == 0


@pytest.mark.asyncio
async def test_drain_queue_processes_rows():
    db = _db()
    resolver = _resolver(db)
    call_count = {"n": 0}

    async def claim_once():
        if call_count["n"] == 0:
            call_count["n"] += 1
            return {"id": "q1", "memory_id": "m1", "retry_count": 0}
        return None

    with patch.object(resolver, "_claim_one", AsyncMock(side_effect=claim_once)), patch.object(
        resolver, "_handle_queue_row", AsyncMock()
    ):
        result = await resolver._drain_queue()

    assert result == 1


# ── _check_escalation_needed ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_escalation_needed_true():
    db = _db(fetchval=3)
    resolver = _resolver(db)
    assert await resolver._check_escalation_needed("mem-x") is True


@pytest.mark.asyncio
async def test_check_escalation_needed_false():
    db = _db(fetchval=2)
    resolver = _resolver(db)
    assert await resolver._check_escalation_needed("mem-x") is False


@pytest.mark.asyncio
async def test_check_escalation_needed_none_fetchval():
    db = _db(fetchval=None)
    resolver = _resolver(db)
    assert await resolver._check_escalation_needed("mem-x") is False


# ── _claim_one — pool async context manager ───────────────────────────────────


def _pool_with_row(row):
    """Build a mock DB with a .pool that returns *row* from fetchrow."""
    from contextlib import asynccontextmanager

    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)

    @asynccontextmanager
    async def fake_transaction():
        yield

    conn.transaction = fake_transaction

    class FakeAcquire:
        async def __aenter__(self_):
            return conn

        async def __aexit__(self_, *a):
            pass

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=FakeAcquire())

    db = _db()
    db.pool = pool
    return db


@pytest.mark.asyncio
async def test_claim_one_returns_row_when_pending():
    sentinel = {"id": "q1", "memory_id": "m1", "status": "processing", "retry_count": 0}
    db = _pool_with_row(sentinel)
    resolver = _resolver(db)
    result = await resolver._claim_one()
    assert result is not None
    assert result["id"] == "q1"


@pytest.mark.asyncio
async def test_claim_one_returns_none_when_empty():
    db = _pool_with_row(None)
    resolver = _resolver(db)
    result = await resolver._claim_one()
    assert result is None


@pytest.mark.asyncio
async def test_claim_one_returns_none_when_no_pool():
    db = _db()
    db.pool = None
    resolver = _resolver(db)
    result = await resolver._claim_one()
    assert result is None


# ── _check_memory — contradiction detection without live DB ───────────────────


def _memory_row(id="m1", content="I prefer Python", trust=0.8, mtype="preference"):
    return {
        "id": id,
        "content": content,
        "trust_score": trust,
        "memory_type": mtype,
        "created_at": None,
        "embedding": None,
    }


@pytest.mark.asyncio
async def test_check_memory_not_found_returns_zero():
    db = _db(fetchrow=None)
    resolver = _resolver(db)
    result = await resolver._check_memory("missing-id")
    assert result == 0


@pytest.mark.asyncio
async def test_check_memory_embed_failure_returns_zero():
    db = _db(fetchrow=_memory_row())
    resolver = _resolver(db)
    resolver.embedder.embed_query = AsyncMock(side_effect=RuntimeError("model down"))
    result = await resolver._check_memory("m1")
    assert result == 0


@pytest.mark.asyncio
async def test_check_memory_no_neighbors_returns_zero():
    db = _db(fetchrow=_memory_row())
    db.fetchall = AsyncMock(return_value=[])
    resolver = _resolver(db)
    resolver.embedder.embed_query = AsyncMock(
        return_value=MagicMock(vector=[0.1] * 4)
    )
    result = await resolver._check_memory("m1")
    assert result == 0


@pytest.mark.asyncio
async def test_check_memory_medium_conflict_logs():
    """A MEDIUM contradiction is logged (not resolved) and flags_count > 0."""
    new_row = _memory_row("m-new", "JP works at LegionForge now", 0.8)
    neighbor = _memory_row("m-old", "JP works at ACME Corp", 0.8)
    db = _db(fetchrow=new_row)
    db.fetchall = AsyncMock(return_value=[neighbor])
    db.fetchval = AsyncMock(return_value=0)  # escalation threshold not met

    resolver = _resolver(db)
    resolver.embedder.embed_query = AsyncMock(
        return_value=MagicMock(vector=[0.1] * 4)
    )

    from src.jeli_scoped_mcp.core.contradiction import (
        ContradictionClassifier,
        ContradictionFlag,
        ContradictionSeverity,
        ContradictionType,
    )

    fake_flag = ContradictionFlag(
        memory_id="m-old",
        conflicting_memory_id="m-new",
        contradiction_type=ContradictionType.DIRECT,
        severity=ContradictionSeverity.MEDIUM,
        reason="direct contradiction detected",
    )

    with patch.object(ContradictionClassifier, "classify", return_value=[fake_flag]):
        result = await resolver._check_memory("m-new")

    assert result == 1
