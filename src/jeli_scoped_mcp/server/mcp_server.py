"""MCP server implementation for Scoped Memory Access.

Exposes exactly four tools (capture_memory, search_memory, audit_trail,
verify_chain) over stdio. The `actor` on every call comes from the server's
own identity config, never from tool arguments — an agent cannot impersonate
another writer.
"""

import json
import logging
from typing import Any

from ..config import Settings
from ..database.pool import AsyncPostgresPool
from ..embedding.provider import EmbeddingProvider
from ..tools.memory_tools import MemoryToolError, MemoryTools

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "capture_memory",
        "description": (
            "Append one memory to the hash-chained store. Content is "
            "sanitized; injection-like content is accepted but capped at "
            "external-grade trust (0.3) and flagged."
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
                    "description": ("0.3 external … 0.6 agent-inferred … 1.0 user-stated"),
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
            "Read-only search over currently-valid memories, ranked by trust "
            "then recency. Phase 1: fts (substring) mode."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "mode": {"type": "string", "enum": ["fts"], "default": "fts"},
                "limit": {"type": "integer", "default": 10},
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
]


class ScopedMCPServer:
    """Scoped MCP Server — gates agent access to memory system."""

    def __init__(
        self,
        db: AsyncPostgresPool,
        embedder: EmbeddingProvider,
        settings: Settings,
    ):
        """Initialize the Scoped MCP server."""
        self.db = db
        self.embedder = embedder
        self.settings = settings
        self.tools = MemoryTools(db=db, embedder=embedder, chain_key=settings.chain_key)

    async def dispatch(self, name: str, arguments: dict) -> dict | list:
        """Route one tool call to MemoryTools with the server-side actor."""
        actor = self.settings.agent_actor
        if name == "capture_memory":
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
                mode=arguments.get("mode", "fts"),
                limit=arguments.get("limit", 10),
            )
        if name == "audit_trail":
            return await self.tools.audit_trail(memory_id=arguments["memory_id"], actor=actor)
        if name == "verify_chain":
            return await self.tools.verify_chain()
        raise MemoryToolError(f"unknown tool: {name}")

    async def run_stdio(self):
        """Run MCP server on stdio transport."""
        # Imported here so the tools layer stays testable without the SDK.
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
            except Exception:
                logger.exception("tool %s failed", name)
                result = {"error": "internal error (see server log)"}
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
            "HTTP transport is not implemented; use stdio " "(SCOPED_MCP_TRANSPORT=stdio)"
        )
