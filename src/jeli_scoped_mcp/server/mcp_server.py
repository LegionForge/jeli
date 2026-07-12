"""MCP server implementation for Scoped Memory Access.

Exposes four agent-safe tools over stdio: capture_memory, search_memory,
audit_trail, summarize_session. When inbox_enabled=True (default), every write
goes through the staging inbox and returns immediately — classification and
chain-write happen asynchronously in the InboxWorker. When inbox_enabled=False,
writes go directly to the hash-chain (used in tests / CLI flows).

Server-side authority, never trusted from arguments:
  actor        — the server's own identity config; an agent cannot impersonate
                 another writer.
  trust        — agent-declared trust_score is clamped to
                 settings.agent_trust_ceiling (0.6 = agent-inferred). The ≥0.9
                 "user confirmed" tiers require an actual human in the loop
                 (jeli CLI, inbox review) and are unreachable from MCP (GH #14).

Deliberately NOT exposed to agents: verify_chain (operator function, O(n) scan
— GH #17), redact / revise / invalidate (user-tier state changes — GH #13).
"""

import hashlib
import json
import logging
import re
from typing import Any

from ..config import Settings
from ..database.pool import AsyncPostgresPool
from ..embedding.provider import EmbeddingProvider
from ..graph import GraphStore
from ..reranker.provider import RerankerProvider
from ..security import VALID_CONTENT_CLASSES
from ..tools.memory_tools import (
    SERVER_OWNED_METADATA_KEYS,
    MemoryToolError,
    MemoryTools,
    apply_read_defenses,
)

logger = logging.getLogger(__name__)

# Serializes the per-actor admission count and insert. The inbox is a global
# queue, so a single lock keeps the boundary exact across server processes.
INBOX_FLOOD_LOCK = 0x4A454C49

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
                    "description": (
                        "0.3 external … 0.6 agent-inferred. Values above the "
                        "server's agent ceiling (default 0.6) are clamped — "
                        "user-confirmed tiers require human review."
                    ),
                },
                "content_class": {
                    "type": "string",
                    "enum": sorted(VALID_CONTENT_CLASSES),
                    "default": "general",
                    "description": (
                        "Content category for the two-axis trust model "
                        "(security-doc marks injection-looking reference "
                        "material; only takes effect for user-tier sources)."
                    ),
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
            "vector similarity (returns distance); fts = Postgres full-text "
            "search with websearch syntax (returns rank), ordered by "
            "relevance, trust, recency."
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
                    "description": "Only return memories of this type.",
                },
                "min_trust": {
                    "type": "number",
                    "description": (
                        "Minimum effective (age-decayed) trust; e.g. 0.6 "
                        "excludes external-source and heavily stale content."
                    ),
                },
                "content_class": {
                    "type": "string",
                    "enum": sorted(VALID_CONTENT_CLASSES),
                    "description": "Only return memories of this content class.",
                },
                "project": {
                    "type": "string",
                    "description": (
                        "Only return memories stamped with this project "
                        "(metadata.project at capture time)."
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
        "name": "summarize_session",
        "description": (
            "Submit an end-of-session summary as an episodic memory. Goes "
            "through the same ingestion path as capture_memory (inbox review, "
            "agent-tier trust); the Insights daemon uses session-summary flags "
            "during consolidation."
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
        "name": "search_by_entity",
        "description": (
            "Read-only search for memories that mention a named entity "
            "(fuzzy match on the entity's name or aliases). Returns "
            "search_memory-shaped rows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_name": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["entity_name"],
        },
    },
    {
        "name": "get_entity_graph",
        "description": (
            "Read-only view of one entity: its relations (both directions) "
            "and the number of memories linked to it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"entity_name": {"type": "string"}},
            "required": ["entity_name"],
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
        self.graph = GraphStore()

    def _clamp_trust(self, declared: float) -> tuple[float, bool]:
        """Apply the server-side agent trust ceiling (GH #14).

        Returns (effective_trust, was_clamped). The declared value is kept in
        metadata by the callers so the clamp is visible in the audit trail.
        """
        ceiling = self.settings.agent_trust_ceiling
        declared = float(declared)
        if declared > ceiling:
            return ceiling, True
        return declared, False

    @staticmethod
    def _resolve_content_class(arguments: dict) -> str:
        """content_class may be a top-level arg or nested inside metadata;
        either way it must be a recognised class — it steers quarantine
        behavior at retrieval time (GH #15)."""
        content_class = arguments.get(
            "content_class",
            (arguments.get("metadata") or {}).get("content_class", "general"),
        )
        if content_class not in VALID_CONTENT_CLASSES:
            raise MemoryToolError(
                f"content_class must be one of {sorted(VALID_CONTENT_CLASSES)}"
            )
        return str(content_class)

    @staticmethod
    def _infer_content_class(
        content: str, declared_class: str, source_agent: str | None
    ) -> str:
        """Server-side stigmatisation of externally-sourced content.

        Agents self-declare content_class, so a poisoned agent could label web
        content as 'general' to dodge quarantine wrapping. When an agent (not the
        CLI) writes default-class content that looks web-sourced — a URL, or a
        phrase like 'according to' / 'from the web' — upgrade it to
        'external-untrusted' regardless of what the agent claimed.
        """
        if source_agent is None or declared_class != "general":
            return declared_class
        lowered = content.lower()
        has_url = re.search(r"https?://", content, re.IGNORECASE) is not None
        has_phrase = any(p in lowered for p in ("according to", "from the web"))
        if has_url or has_phrase:
            return "external-untrusted"
        return declared_class

    async def dispatch(self, name: str, arguments: dict) -> dict | list:
        """Route one tool call to MemoryTools with the server-side actor."""
        actor = self.settings.agent_actor
        if name == "capture_memory":
            content_class = self._resolve_content_class(arguments)
            content_class = self._infer_content_class(
                arguments["content"], content_class, actor
            )
            trust, clamped = self._clamp_trust(arguments["trust_score"])
            metadata = dict(arguments.get("metadata") or {})
            # Strip server-owned provenance/security keys an agent must not set
            # (GH #35): forging them impersonates daemon output or downgrades the
            # injection wrap. Internal callers bypass this by using MemoryTools.
            stripped = [k for k in metadata if k in SERVER_OWNED_METADATA_KEYS]
            for k in stripped:
                del metadata[k]
            if stripped:
                logger.warning(
                    "capture_memory: stripped server-owned metadata keys from "
                    "agent input: %s",
                    stripped,
                )
            if clamped:
                metadata["declared_trust"] = float(arguments["trust_score"])
                metadata["trust_clamped_to"] = trust
            arguments = {**arguments, "trust_score": trust, "metadata": metadata}
            if self.settings.inbox_enabled:
                return await self._submit_to_inbox(arguments, actor, content_class)
            return await self.tools.capture_memory(
                content=arguments["content"],
                memory_type=arguments["memory_type"],
                trust_score=trust,
                actor=actor,
                source_agent=actor,
                session_id=arguments.get("session_id"),
                metadata=metadata or None,
                content_class=content_class,
            )
        if name == "search_memory":
            class_filter: str | None = arguments.get("content_class")
            if class_filter is not None and class_filter not in VALID_CONTENT_CLASSES:
                raise MemoryToolError(
                    f"content_class must be one of {sorted(VALID_CONTENT_CLASSES)}"
                )
            return await self.tools.search_memory(
                query=arguments["query"],
                actor=actor,
                mode=arguments.get("mode", "semantic"),
                limit=arguments.get("limit", 10),
                rerank=arguments.get("rerank", False),
                memory_type=arguments.get("memory_type"),
                min_trust=arguments.get("min_trust"),
                content_class=class_filter,
                project=arguments.get("project"),
            )
        if name == "audit_trail":
            return await self.tools.audit_trail(memory_id=arguments["memory_id"], actor=actor)
        if name == "search_by_entity":
            # Apply constitutional ReadGate so entity searches respect the same
            # sovereignty rules as search_memory (exclude_memory_type,
            # min_trust_floor, exclude_content_class, etc.).
            from ..constitutional.gate import ReadGate
            from ..constitutional.manager import ConstitutionalManager

            entity_results = await self.graph.search_by_entity(
                self.db,
                entity_name=arguments["entity_name"],
                limit=arguments.get("limit", 10),
            )
            # Same read-time defenses as search_memory (GH #36): decay + wrap
            # flagged / low-trust-procedural / derived content. Was previously
            # missing here, so the entity surface returned raw, non-decayed rows.
            apply_read_defenses(entity_results)
            active_rules = await ConstitutionalManager().load_active_rules(self.db)
            if active_rules:
                entity_results = ReadGate().apply(entity_results, actor=actor, rules=active_rules)
            return entity_results
        if name == "get_entity_graph":
            return await self.graph.get_entity_graph(
                self.db, entity_name=arguments["entity_name"]
            )
        if name == "summarize_session":
            # Same gate as any other agent write: inbox when enabled, agent-tier
            # trust always. The old path stored summaries at 0.9 directly on the
            # chain — an unreviewed high-trust bypass of the Bouncer (GH #12).
            trust = self.settings.agent_trust_ceiling
            summary_args = {
                "content": arguments["content"],
                "memory_type": "episodic",
                "trust_score": trust,
                "session_id": arguments.get("session_id"),
                "metadata": {"is_session_summary": True},
            }
            if self.settings.inbox_enabled:
                return await self._submit_to_inbox(summary_args, actor, "general")
            return await self.tools.summarize_session(
                content=arguments["content"],
                actor=actor,
                session_id=arguments.get("session_id"),
                trust_score=trust,
            )
        raise MemoryToolError(f"unknown tool: {name}")

    async def _submit_to_inbox(
        self, arguments: dict, actor: str, content_class: str = "general"
    ) -> dict:
        """Write to inbox and return immediately — non-blocking."""
        content = arguments["content"]
        if not content or not content.strip():
            raise MemoryToolError("content must be non-empty")

        source_metadata = arguments.get("metadata") or None

        caller_trust = float(arguments["trust_score"])
        flood_control_enabled = self.settings.inbox_flood_max_low_trust > 0
        async with self.db.locked_transaction(INBOX_FLOOD_LOCK) as conn:
            recent_count = 0
            if flood_control_enabled and caller_trust <= self.settings.inbox_flood_trust_ceiling:
                recent_count = await conn.fetchval(
                    """
                    SELECT count(*)
                    FROM memory_inbox
                    WHERE source_agent = $1
                      AND caller_trust <= $2
                      AND submitted_at >= now() - ($3 * interval '1 second')
                    """,
                    actor,
                    self.settings.inbox_flood_trust_ceiling,
                    self.settings.inbox_flood_window_seconds,
                )

            held = (
                recent_count >= self.settings.inbox_flood_max_low_trust
                if flood_control_enabled
                else False
            )
            status = "held" if held else "pending"
            review_reason = "source_flood_limit" if held else None
            row = await conn.fetchrow(
                """
                INSERT INTO memory_inbox (
                    content, content_hash, source_agent, session_id,
                    caller_trust, caller_type, content_class, source_metadata,
                    status, requires_review, review_reason
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11)
                RETURNING id, submitted_at, status, review_reason
                """,
                content,
                _content_hash(content),
                actor,
                arguments.get("session_id"),
                caller_trust,
                arguments["memory_type"],
                content_class,
                json.dumps(source_metadata) if source_metadata else None,
                status,
                held,
                review_reason,
            )
        if row is None:
            raise MemoryToolError("inbox insert failed")

        log = logger.warning if held else logger.info
        log(
            "capture_memory: %s in inbox id=%s actor=%s type=%s",
            status,
            row["id"],
            actor,
            arguments["memory_type"],
        )
        return {
            "inbox_id": str(row["id"]),
            "status": "held" if row["status"] == "held" else "queued",
            "submitted_at": row["submitted_at"].isoformat(),
            **({"review_reason": row["review_reason"]} if row["review_reason"] else {}),
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
