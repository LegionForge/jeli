"""Unit tests for InsightsDaemon LLM synthesis + contradiction surfacing, and
the `jeli verify --report` integrity report. All DB/LLM calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.jeli_scoped_mcp.config import Settings
from src.jeli_scoped_mcp.daemons.insights import InsightsDaemon


def _settings(litellm_base_url: str = "") -> Settings:
    # litellm_base_url has a validation_alias, so it cannot be set via the field
    # kwarg; assign on the instance to override any value picked up from .env/env.
    s = Settings(chain_key="test-chain-key-32-bytes-minimum!!")
    s.litellm_base_url = litellm_base_url
    return s


def _memory_tools():
    mt = MagicMock()
    mt.capture_memory = AsyncMock(return_value={"id": "m1", "record_hash": "x"})
    return mt


def _cluster_db(main_rows, neighbor_rows):
    """DB whose fetchall returns main_rows first, then neighbor_rows per row."""
    db = MagicMock()
    db.fetchall = AsyncMock(side_effect=[main_rows, neighbor_rows])
    db.execute = AsyncMock(return_value="INSERT 1")
    db.pool = None
    return db


def _daemon(db, settings):
    return InsightsDaemon(
        db=db, embedder=MagicMock(), memory_tools=_memory_tools(), settings=settings
    )


# ── cluster synthesis ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cluster_scan_uses_fallback_when_no_llm():
    """LLM call raising must not crash the daemon; a cluster memory is still written."""
    main = [{"id": "a", "content": "alpha content", "memory_type": "semantic", "embedding": "[0.1]"}]
    neighbors = [
        {"id": "b", "content": "beta content"},
        {"id": "c", "content": "gamma content"},
    ]
    db = _cluster_db(main, neighbors)
    daemon = _daemon(db, _settings(litellm_base_url="http://proxy"))

    with patch.object(daemon, "_call_synthesis_llm", AsyncMock(side_effect=RuntimeError("boom"))):
        result = await daemon._cluster_scan()

    assert result["clusters_found"] == 1
    assert result["synthesis_used"] is False
    written = daemon.memory_tools.capture_memory.await_args.kwargs["content"]
    assert written.startswith("Cluster: ")


@pytest.mark.asyncio
async def test_cluster_summary_format_with_fallback():
    """With no LLM configured the summary is a 'Cluster: snippet | snippet' list."""
    main = [{"id": "a", "content": "alpha", "memory_type": "semantic", "embedding": "[0.1]"}]
    neighbors = [
        {"id": "b", "content": "beta"},
        {"id": "c", "content": "gamma"},
    ]
    db = _cluster_db(main, neighbors)
    daemon = _daemon(db, _settings())  # litellm_base_url == "" → no LLM

    result = await daemon._cluster_scan()

    assert result["synthesis_used"] is False
    written = daemon.memory_tools.capture_memory.await_args.kwargs["content"]
    assert written.startswith("Cluster: ")
    assert " | " in written


@pytest.mark.asyncio
async def test_synthesis_used_field_in_result():
    """A successful LLM call produces an 'Insight: ...' memory and synthesis_used=True."""
    main = [{"id": "a", "content": "alpha", "memory_type": "semantic", "embedding": "[0.1]"}]
    neighbors = [
        {"id": "b", "content": "beta"},
        {"id": "c", "content": "gamma"},
    ]
    db = _cluster_db(main, neighbors)
    daemon = _daemon(db, _settings(litellm_base_url="http://proxy"))

    with patch.object(
        daemon, "_call_synthesis_llm", AsyncMock(return_value="These memories share a theme.")
    ):
        result = await daemon._cluster_scan()

    assert "synthesis_used" in result
    assert result["synthesis_used"] is True
    written = daemon.memory_tools.capture_memory.await_args.kwargs["content"]
    assert written == "Insight: These memories share a theme."


# ── contradiction surfacing ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_contradiction_surfacing_flags_stuck():
    row = {"id": "q1", "memory_id": "m1", "error": "unresolvable"}
    db = MagicMock()
    db.fetchall = AsyncMock(return_value=[row])
    db.execute = AsyncMock(return_value="INSERT 1")
    daemon = _daemon(db, _settings())

    result = await daemon._contradiction_surfacing()

    assert result["stuck_conflicts_flagged"] == 1
    call_args = db.execute.await_args.args
    # sql, memory_id, actor, details_json
    assert "contradiction_surfacing_needed" in call_args[0]
    assert call_args[1] == "m1"
    assert call_args[2] == daemon.actor


@pytest.mark.asyncio
async def test_contradiction_surfacing_ignores_fresh_failures():
    """Fresh (<24h) failures are filtered by the query, so nothing is flagged."""
    db = MagicMock()
    db.fetchall = AsyncMock(return_value=[])
    db.execute = AsyncMock(return_value="INSERT 1")
    daemon = _daemon(db, _settings())

    result = await daemon._contradiction_surfacing()

    assert result["stuck_conflicts_flagged"] == 0
    db.execute.assert_not_awaited()


# ── jeli verify --report ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_report_structure():
    from src.jeli_scoped_mcp import cli

    db = MagicMock()
    db.connect = AsyncMock()
    db.close = AsyncMock()
    db.fetchall = AsyncMock(
        side_effect=[
            [{"memory_type": "semantic", "count": 5}],          # by_type
            [{"content_class": "general", "count": 5}],         # by_class
        ]
    )
    db.fetchrow = AsyncMock(
        side_effect=[
            {"total": 10, "high_trust": 4, "medium_trust": 3, "low_trust": 3, "avg_trust": 0.65},
            {"stuck_conflicts": 2},
            {"orphaned_state_events": 0},
            {"memories_without_audit": 1},
            {"aging_high_trust": 3},
        ]
    )

    tools = MagicMock()
    tools.verify_chain = AsyncMock(
        return_value={"chain_valid": True, "records_checked": 10, "first_bad_record": None}
    )
    state = MagicMock()
    state.verify = AsyncMock(
        return_value={"state_chain_valid": True, "events_checked": 3, "cache_consistent": True}
    )

    settings = _settings()
    with patch.object(cli, "AsyncPostgresPool", return_value=db), \
         patch.object(cli, "MemoryTools", return_value=tools), \
         patch.object(cli, "StateTools", return_value=state):
        report = await cli._run_integrity_report(settings)

    assert report["chain_valid"] is True
    assert report["records_checked"] == 10
    assert report["state_chain_valid"] is True
    assert report["memory_stats"]["total"] == 10
    assert report["memory_stats"]["by_type"] == {"semantic": 5}
    assert report["memory_stats"]["by_content_class"] == {"general": 5}
    assert report["trust_distribution"]["high_trust"] == 4
    assert report["trust_distribution"]["avg_trust"] == 0.65
    assert report["stuck_conflicts"] == 2
    assert report["orphaned_state_events"] == 0
    assert report["memories_without_audit"] == 1
    assert report["aging_high_trust"] == 3


# ── cluster scan — uncovered branches ────────────────────────────────────────


@pytest.mark.asyncio
async def test_cluster_scan_skips_undersized_cluster():
    """A memory with only 1 neighbor (2 total) is below MIN_CLUSTER_SIZE=3 — no write."""
    main = [{"id": "a", "content": "alpha", "memory_type": "semantic", "embedding": "[0.1]"}]
    neighbors = [{"id": "b", "content": "beta"}]  # 2 members total < 3
    db = _cluster_db(main, neighbors)
    daemon = _daemon(db, _settings())

    result = await daemon._cluster_scan()

    assert result["clusters_found"] == 0
    daemon.memory_tools.capture_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_cluster_scan_skips_null_embedding():
    """A memory whose embedding is None is skipped — no cluster attempt."""
    main = [{"id": "a", "content": "alpha", "memory_type": "semantic", "embedding": None}]
    db = MagicMock()
    db.fetchall = AsyncMock(return_value=main)  # no second fetchall needed
    db.pool = None
    daemon = _daemon(db, _settings())

    result = await daemon._cluster_scan()

    assert result["clusters_found"] == 0


@pytest.mark.asyncio
async def test_cluster_scan_skips_empty_text_synthesis():
    """_synthesize_cluster returns fallback when LLM returns empty string."""
    main = [{"id": "a", "content": "alpha", "memory_type": "semantic", "embedding": "[0.1]"}]
    neighbors = [{"id": "b", "content": "beta"}, {"id": "c", "content": "gamma"}]
    db = _cluster_db(main, neighbors)
    daemon = _daemon(db, _settings(litellm_base_url="http://proxy"))

    with patch.object(daemon, "_call_synthesis_llm", AsyncMock(return_value="")):
        result = await daemon._cluster_scan()

    # Empty LLM response falls back to Cluster: snippet format
    assert result["clusters_found"] == 1
    assert result["synthesis_used"] is False
    written = daemon.memory_tools.capture_memory.await_args.kwargs["content"]
    assert written.startswith("Cluster: ")


# ── anti-laundering: trust inheritance + flagged exclusion (MemLineage) ──────


@pytest.mark.asyncio
async def test_cluster_trust_inherits_minimum_of_sources():
    """Derived insight trust = min(source trusts), not a flat base."""
    main = [{"id": "a", "content": "alpha", "memory_type": "semantic",
             "embedding": "[0.1]", "trust_score": 0.9}]
    neighbors = [
        {"id": "b", "content": "beta", "trust_score": 0.3},
        {"id": "c", "content": "gamma", "trust_score": 0.6},
    ]
    db = _cluster_db(main, neighbors)
    daemon = _daemon(db, _settings())

    await daemon._cluster_scan()

    kwargs = daemon.memory_tools.capture_memory.await_args.kwargs
    assert kwargs["trust_score"] == pytest.approx(0.3)  # weakest source wins
    assert kwargs["metadata"]["derived_from"] == ["a", "b", "c"]
    assert kwargs["metadata"]["source_trust_min"] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_cluster_trust_capped_at_base_even_for_trusted_sources():
    """All-high-trust sources still cap at CLUSTER_BASE_TRUST — a synthesis is
    agent-derived content, never user-tier."""
    main = [{"id": "a", "content": "alpha", "memory_type": "semantic",
             "embedding": "[0.1]", "trust_score": 1.0}]
    neighbors = [
        {"id": "b", "content": "beta", "trust_score": 0.9},
        {"id": "c", "content": "gamma", "trust_score": 0.9},
    ]
    db = _cluster_db(main, neighbors)
    daemon = _daemon(db, _settings())

    await daemon._cluster_scan()

    kwargs = daemon.memory_tools.capture_memory.await_args.kwargs
    assert kwargs["trust_score"] == pytest.approx(daemon.CLUSTER_BASE_TRUST)


@pytest.mark.asyncio
async def test_cluster_queries_exclude_injection_flagged():
    """Both the seed query and the neighbor query filter out flagged memories,
    so quarantined content never reaches the synthesis LLM."""
    main = [{"id": "a", "content": "alpha", "memory_type": "semantic",
             "embedding": "[0.1]", "trust_score": 0.8}]
    neighbors = [
        {"id": "b", "content": "beta", "trust_score": 0.8},
        {"id": "c", "content": "gamma", "trust_score": 0.8},
    ]
    db = _cluster_db(main, neighbors)
    daemon = _daemon(db, _settings())

    await daemon._cluster_scan()

    seed_query = db.fetchall.await_args_list[0].args[0]
    neighbor_query = db.fetchall.await_args_list[1].args[0]
    assert "injection_flagged" in seed_query
    assert "injection_flagged" in neighbor_query
