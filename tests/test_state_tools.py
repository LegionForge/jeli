"""Unit tests for StateTools (revise / invalidate / two-chain verify)."""

import pytest
from test_memory_tools import CHAIN_KEY, FakeEmbedder, FakePool, capture

from jeli_scoped_mcp.tools.memory_tools import MemoryToolError, MemoryTools
from jeli_scoped_mcp.tools.state_tools import StateTools


@pytest.fixture
def pool():
    return FakePool()


@pytest.fixture
def tools(pool):
    return MemoryTools(db=pool, embedder=FakeEmbedder(), chain_key=CHAIN_KEY)


@pytest.fixture
def state(pool, tools):
    return StateTools(db=pool, memory_tools=tools, chain_key=CHAIN_KEY, key_id="k1")


# ── invalidate ───────────────────────────────────────────────────────────────


async def test_invalidate_chains_event_and_sets_cache(state, tools, pool):
    r = await capture(tools)
    out = await state.invalidate(r["id"], reason="test retire", actor="jp")
    assert out["event_type"] == "invalidated"
    assert pool.memories[0]["valid_until"] is not None
    assert len(pool.state_events) == 1
    assert pool.state_events[0]["prev_hash"] is None
    assert any(a["action"] == "invalidated" for a in pool.audit if "action" in a)


async def test_invalidate_requires_reason(state, tools):
    r = await capture(tools)
    with pytest.raises(MemoryToolError, match="reason"):
        await state.invalidate(r["id"], reason="  ", actor="jp")


async def test_invalidate_unknown_memory(state):
    import uuid

    with pytest.raises(MemoryToolError, match="not found"):
        await state.invalidate(str(uuid.uuid4()), reason="x", actor="jp")


async def test_double_retire_refused(state, tools):
    r = await capture(tools)
    await state.invalidate(r["id"], reason="once", actor="jp")
    with pytest.raises(MemoryToolError, match="already retired"):
        await state.invalidate(r["id"], reason="twice", actor="jp")


async def test_state_events_chain_links(state, tools, pool):
    a = await capture(tools, content="first fact")
    b = await capture(tools, content="second fact")
    await state.invalidate(a["id"], reason="r1", actor="jp")
    await state.invalidate(b["id"], reason="r2", actor="jp")
    assert pool.state_events[1]["prev_hash"] == pool.state_events[0]["record_hash"]


# ── revise ───────────────────────────────────────────────────────────────────


async def test_revise_creates_successor_and_supersedes(state, tools, pool):
    r = await capture(tools, content="JP prefers YAML")
    out = await state.revise(r["id"], "JP prefers TOML over YAML", reason="correcting", actor="jp")
    original = pool.memories[0]
    assert str(original["superseded_by"]) == out["successor"]["id"]
    assert original["valid_until"] is not None
    successor = pool.memories[1]
    assert str(successor["amended_from"]) == str(r["id"])
    # retired memory leaves search; successor findable
    hits = await tools.search_memory(query="toml", actor="jp")
    assert [h["id"] for h in hits] == [out["successor"]["id"]]


async def test_revise_retired_memory_refused(state, tools):
    r = await capture(tools)
    await state.invalidate(r["id"], reason="retire", actor="jp")
    with pytest.raises(MemoryToolError, match="already retired"):
        await state.revise(r["id"], "new text", reason="late", actor="jp")


# ── verify: chain walk + cache cross-check ──────────────────────────────────


async def test_verify_intact(state, tools):
    r = await capture(tools)
    await state.invalidate(r["id"], reason="retire", actor="jp")
    v = await state.verify()
    assert v["state_chain_valid"] is True
    assert v["cache_consistent"] is True
    assert v["events_checked"] == 1


async def test_verify_empty(state):
    v = await state.verify()
    assert v == {
        "state_chain_valid": True,
        "events_checked": 0,
        "first_bad_event": None,
        "cache_consistent": True,
        "mismatches": [],
    }


async def test_verify_detects_tampered_event(state, tools, pool):
    r = await capture(tools)
    await state.invalidate(r["id"], reason="honest reason", actor="jp")
    pool.state_events[0]["reason"] = "forged reason"
    v = await state.verify()
    assert v["state_chain_valid"] is False
    assert v["first_bad_event"] == str(pool.state_events[0]["id"])


async def test_verify_detects_cache_without_event(state, tools, pool):
    from datetime import UTC, datetime

    await capture(tools)
    pool.memories[0]["valid_until"] = datetime.now(UTC)  # column flip, no event
    v = await state.verify()
    assert v["cache_consistent"] is False
    assert "no chained event" in v["mismatches"][0]


async def test_verify_detects_event_without_cache(state, tools, pool):
    r = await capture(tools)
    await state.invalidate(r["id"], reason="retire", actor="jp")
    pool.memories[0]["valid_until"] = None  # resurrect via column flip
    pool.memories[0]["superseded_by"] = None
    v = await state.verify()
    assert v["cache_consistent"] is False
    assert "columns not set" in v["mismatches"][0]


async def test_verify_unknown_key_fails_closed(state, tools, pool):
    r = await capture(tools)
    await state.invalidate(r["id"], reason="retire", actor="jp")
    pool.state_events[0]["key_id"] = "k9"
    v = await state.verify()
    assert v["state_chain_valid"] is False
