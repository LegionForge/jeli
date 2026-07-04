"""MCP server implementation for Scoped Memory Access.

Exposes four tools over stdio. When inbox_enabled=True (default), capture_memory
writes to the staging inbox and returns immediately — classification and chain-write
happen asynchronously in the InboxWorker. When inbox_enabled=False, writes go
directly to the hash-chain (used in tests / CLI flows).

The `actor` on every call comes from the server's own identity config, never from
tool arguments — an agent cannot impersonate another writer.
"""

import hashlib
import json
import logging
import re
from typing import Any

from ..config import Settings
from ..database.pool import AsyncPostgresPool
from ..embedding.provider import EmbeddingProvider
from ..reranker.provider import RerankerProvider
from ..tools.memory_tools import MemoryToolError, MemoryTools

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "capture_memory",
        "description": (
            "Submit a memory for ingestion. When the inbox is enabled (default), "
            "returns {inbox_id, status: 'queued'} immediately — the Bouncer classifies "
            "and chain-writes asynchronously. When inbox is disabled, writes directly "
            "and returns {id, record_hash, trust_score}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The memory text"},
                "memory_type": {
                    "type": "string",
                    "enum": [
                        "preference",
                        "identity",
                        "episodic",
                        "semantic",
                        "procedural",
                        "transient",
                    ],
                },
                "trust_score": {
                    "type": "number",
                    "description": "0.3 external … 0.6 agent-inferred … 1.0 user-stated",
                },
                "session_id": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["content", "memory_type", "trust_score"],
        },
    },
    {
        "name": "search_memory",
        "description": (
            "Read-only search over currently-valid memories. semantic = "
            "vector similarity (returns distance); fts = substring, ranked "
            "by trust then recency."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["fts", "semantic"],
                    "default": "semantic",
                },
                "limit": {"type": "integer", "default": 10},
                "rerank": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Re-rank results using LLM relevance scoring (slower but "
                        "more accurate). Only applies to mode=semantic."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "audit_trail",
        "description": (
            "Provenance for one memory: origin, trust, amendment links, all "
            "audit events, and whether its hash still verifies."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"memory_id": {"type": "string"}},
            "required": ["memory_id"],
        },
    },
    {
        "name": "verify_chain",
        "description": (
            "Recompute every record hash oldest→newest; returns chain_valid "
            "and the first tampered record id, if any."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "summarize_session",
        "description": (
            "Store an end-of-session summary as an episodic memory (trust 0.9). "
            "Call this at session end with a written summary of what happened; "
            "the Insights daemon uses session-summary flags during consolidation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The session summary text to store",
                },
                "session_id": {"type": "string"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "redact",
        "description": (
            "Redact a memory's content. The hash-chain record and audit trail "
            "are preserved so the redaction itself is auditable. The record is "
            "marked invalid and will not appear in search results."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why this memory is being redacted",
                },
            },
            "required": ["memory_id", "reason"],
        },
    },
]


def _content_hash(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content.lower().strip())
    return hashlib.sha256(normalized.encode()).hexdigest()


class ScopedMCPServer:
    """Scoped MCP Server — gates agent access to memory system."""

    def __init__(
        self,
        db: AsyncPostgresPool,
        embedder: EmbeddingProvider,
        settings: Settings,
    ):
        self.db = db
        self.embedder = embedder
        self.settings = settings
        self.reranker = RerankerProvider.from_settings(settings)
        self.tools = MemoryTools(
            db=db,
            embedder=embedder,
            chain_key=settings.chain_key,
            key_id=settings.chain_key_id,
            reranker=self.reranker,
        )

    async def dispatch(self, name: str, arguments: dict) -> dict | list:
        """Route one tool call to MemoryTools with the server-side actor."""
        actor = self.settings.agent_actor
        if name == "capture_memory":
            if self.settings.inbox_enabled:
                return await self._submit_to_inbox(arguments, actor)
            return await self.tools.capture_memory(
                content=arguments["content"],
                memory_type=arguments["memory_type"],
                trust_score=arguments["trust_score"],
                actor=actor,
                source_agent=actor,
                session_id=arguments.get("session_id"),
                metadata=arguments.get("metadata"),
            )
        if name == "search_memory":
            return await self.tools.search_memory(
                query=arguments["query"],
                actor=actor,
                mode=arguments.get("mode", "semantic"),
                limit=arguments.get("limit", 10),
                rerank=arguments.get("rerank", False),
            )
        if name == "audit_trail":
            return await self.tools.audit_trail(memory_id=arguments["memory_id"], actor=actor)
        if name == "verify_chain":
            return await self.tools.verify_chain()
        if name == "summarize_session":
            return await self.tools.summarize_session(
                content=arguments["content"],
                actor=actor,
                session_id=arguments.get("session_id"),
            )
        if name == "redact":
            return await self.tools.redact(
                memory_id=arguments["memory_id"],
                reason=arguments["reason"],
                actor=actor,
            )
        raise MemoryToolError(f"unknown tool: {name}")

    async def _submit_to_inbox(self, arguments: dict, actor: str) -> dict:
        """Write to inbox and return immediately — non-blocking."""
        content = arguments["content"]
        if not content or not content.strip():
            raise MemoryToolError("content must be non-empty")

        row = await self.db.fetchrow(
            """
            INSERT INTO memory_inbox (
                content, content_hash, source_agent, session_id,
                caller_trust, caller_type
            ) VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, submitted_at
            """,
            content,
            _content_hash(content),
            actor,
            arguments.get("session_id"),
            float(arguments["trust_score"]),
            arguments["memory_type"],
        )
        if row is None:
            raise MemoryToolError("inbox insert failed")

        logger.info(
            "capture_memory: queued to inbox id=%s actor=%s type=%s",
            row["id"],
            actor,
            arguments["memory_type"],
        )
        return {
            "inbox_id": str(row["id"]),
            "status": "queued",
            "submitted_at": row["submitted_at"].isoformat(),
        }

    async def run_stdio(self):
        """Run MCP server on stdio transport."""
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool

        server = Server("jeli-scoped-mcp")

        @server.list_tools()
        async def list_tools() -> list[Tool]:
            return [Tool(**t) for t in TOOL_DEFINITIONS]

        @server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            try:
                result = await self.dispatch(name, arguments or {})
            except MemoryToolError as e:
                result = {"error": str(e)}
            except Exception as exc:
                exc_type = type(exc).__name__
                exc_msg = str(exc)
                logger.exception("tool %s failed", name)
                # Surface enough detail for the calling agent to self-correct,
                # without leaking internal credentials or stack traces.
                result = {
                    "error": "internal error",
                    "detail": f"{exc_type}: {exc_msg[:300]}",
                    "tool": name,
                    "hint": (
                        "If this is a DB error, the migration may not be applied. "
                        "Run: alembic upgrade head"
                        if "does not exist" in exc_msg or "UndefinedTable" in exc_type
                        else "Check server logs for full traceback."
                    ),
                }
            return [TextContent(type="text", text=json.dumps(result))]

        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    async def run_http(self):
        """Run MCP server on HTTP transport (dev/testing only)."""
        raise NotImplementedError(
            "HTTP transport is not implemented; use stdio (SCOPED_MCP_TRANSPORT=stdio)"
        )
