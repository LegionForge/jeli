"""Tests for the ScopedMCPServer dispatch layer — the agent-facing boundary.

Covers the server-side authority guarantees (GH #12, #14, #15, #17):
trust clamping, content_class validation, summarize_session going through
the inbox, and the retired tools (verify_chain, redact) being unreachable.
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from jeli_scoped_mcp.config import Settings
from jeli_scoped_mcp.server.mcp_server import TOOL_DEFINITIONS, ScopedMCPServer
from jeli_scoped_mcp.tools.memory_tools import MemoryToolError

AGENT_TOOLS = {t["name"] for t in TOOL_DEFINITIONS}


def _settings(**overrides) -> Settings:
    defaults = {
        "chain_key": "test-chain-key-32-bytes-minimum!!",
        "agent_actor": "test-agent",
        "inbox_enabled": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _server(settings: Settings) -> ScopedMCPServer:
    db = MagicMock()
    db.fetchrow = AsyncMock(
        return_value={
            "id": "11111111-1111-1111-1111-111111111111",
            "submitted_at": datetime.now(UTC),
        }
    )
    server = ScopedMCPServer.__new__(ScopedMCPServer)
    server.db = db
    server.settings = settings
    server.tools = MagicMock()
    for method in ("capture_memory", "search_memory", "audit_trail", "summarize_session"):
        setattr(server.tools, method, AsyncMock(return_value={}))
    return server


# ── tool surface ─────────────────────────────────────────────────────────────


def test_agent_surface_has_no_operator_tools():
    """verify_chain is an operator function (GH #17); redact/revise/invalidate
    are user-tier state changes (GH #13). None may be agent-callable."""
    assert AGENT_TOOLS == {
        "capture_memory",
        "search_memory",
        "audit_trail",
        "summarize_session",
    }


async def test_dispatch_rejects_retired_tools():
    server = _server(_settings())
    for name in ("verify_chain", "redact", "revise", "invalidate"):
        with pytest.raises(MemoryToolError, match="unknown tool"):
            await server.dispatch(name, {})


def test_capture_schema_declares_content_class():
    capture = next(t for t in TOOL_DEFINITIONS if t["name"] == "capture_memory")
    prop = capture["inputSchema"]["properties"]["content_class"]
    assert set(prop["enum"]) == {
        "general",
        "security-doc",
        "code-sample",
        "external-untrusted",
    }


# ── trust clamping (GH #14) ──────────────────────────────────────────────────


async def test_capture_clamps_agent_trust_to_ceiling():
    server = _server(_settings())
    await server.dispatch(
        "capture_memory",
        {"content": "x", "memory_type": "episodic", "trust_score": 0.95},
    )
    args = server.db.fetchrow.await_args.args
    caller_trust = args[5]  # $5 in the inbox INSERT
    assert caller_trust == 0.6
    source_metadata = json.loads(args[8])
    assert source_metadata["declared_trust"] == 0.95
    assert source_metadata["trust_clamped_to"] == 0.6


async def test_capture_below_ceiling_not_clamped():
    server = _server(_settings())
    await server.dispatch(
        "capture_memory",
        {"content": "x", "memory_type": "episodic", "trust_score": 0.3},
    )
    args = server.db.fetchrow.await_args.args
    assert args[5] == 0.3
    assert args[8] is None  # no metadata injected when nothing was clamped


async def test_capture_clamps_on_direct_path_too():
    server = _server(_settings(inbox_enabled=False))
    await server.dispatch(
        "capture_memory",
        {"content": "x", "memory_type": "episodic", "trust_score": 1.0},
    )
    kwargs = server.tools.capture_memory.await_args.kwargs
    assert kwargs["trust_score"] == 0.6
    assert kwargs["metadata"]["declared_trust"] == 1.0


# ── summarize_session through the Bouncer (GH #12) ───────────────────────────


async def test_summarize_session_goes_through_inbox():
    server = _server(_settings())
    result = await server.dispatch("summarize_session", {"content": "session recap"})
    assert result["status"] == "queued"
    server.tools.summarize_session.assert_not_awaited()
    args = server.db.fetchrow.await_args.args
    assert args[5] == 0.6  # agent ceiling, not 0.9
    assert args[6] == "episodic"
    assert json.loads(args[8])["is_session_summary"] is True


async def test_summarize_session_direct_path_capped():
    server = _server(_settings(inbox_enabled=False))
    await server.dispatch("summarize_session", {"content": "session recap"})
    kwargs = server.tools.summarize_session.await_args.kwargs
    assert kwargs["trust_score"] == 0.6


# ── content_class validation (GH #15) ────────────────────────────────────────


async def test_capture_rejects_unknown_content_class():
    server = _server(_settings())
    with pytest.raises(MemoryToolError, match="content_class"):
        await server.dispatch(
            "capture_memory",
            {
                "content": "x",
                "memory_type": "episodic",
                "trust_score": 0.5,
                "content_class": "totally-legit",
            },
        )
    server.db.fetchrow.assert_not_awaited()


async def test_capture_rejects_unknown_content_class_in_metadata():
    server = _server(_settings())
    with pytest.raises(MemoryToolError, match="content_class"):
        await server.dispatch(
            "capture_memory",
            {
                "content": "x",
                "memory_type": "episodic",
                "trust_score": 0.5,
                "metadata": {"content_class": "nope"},
            },
        )


async def test_capture_accepts_valid_content_class():
    server = _server(_settings())
    await server.dispatch(
        "capture_memory",
        {
            "content": "x",
            "memory_type": "semantic",
            "trust_score": 0.5,
            "content_class": "security-doc",
        },
    )
    args = server.db.fetchrow.await_args.args
    assert args[7] == "security-doc"


# ── actor authority ──────────────────────────────────────────────────────────


async def test_actor_is_server_side_even_if_argument_passed():
    server = _server(_settings(inbox_enabled=False))
    await server.dispatch(
        "capture_memory",
        {
            "content": "x",
            "memory_type": "episodic",
            "trust_score": 0.5,
            "actor": "forged-identity",
        },
    )
    kwargs = server.tools.capture_memory.await_args.kwargs
    assert kwargs["actor"] == "test-agent"
