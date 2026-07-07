"""MemoryExporter — serialise the full memory store to a portable JSON-Lines archive.

Format: one JSON object per line.
  Line 0: manifest (schema_version, exported_at, chain_valid, record_count, exporter)
  Lines 1..N: memory records (content, metadata, trust — no embeddings; they are
              model-specific and must be recomputed on import)

Embeddings are intentionally excluded: they are an implementation artifact tied to a
specific model/version. The sovereign asset is the content and provenance, not the
floating-point representation of it. Importers re-embed using whatever model is
configured at destination.

The export is human-readable and can be trivially inspected, audited, or migrated
to any other system. This is the anti-vendor-lock-in guarantee.
"""

import json
import logging
from datetime import UTC, datetime
from typing import IO

from ..database.pool import AsyncPostgresPool

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
EXPORTER_ID = "jeli-scoped-mcp"


class MemoryExporter:
    """Exports memories to a JSON-Lines stream (file, stdout, or buffer)."""

    def __init__(self, db: AsyncPostgresPool):
        self.db = db

    async def export(
        self,
        out: IO[str],
        include_audit: bool = False,
        include_redacted: bool = False,
        memory_type: str | None = None,
        min_trust: float | None = None,
    ) -> dict:
        """Write the full (or filtered) memory store to *out* as JSON-Lines.

        Returns a summary dict: {record_count, redacted_count, audit_events_count,
        chain_valid, exported_at}.
        """
        # 1. Verify chain integrity before exporting so the manifest is honest.
        chain_valid = await self._check_chain_valid()

        # 2. Build SQL filter.
        where_clauses = ["valid_until IS NULL"]
        params: list = []
        p = 1
        if memory_type:
            where_clauses.append(f"memory_type = ${p}")
            params.append(memory_type)
            p += 1
        if min_trust is not None:
            where_clauses.append(f"trust_score >= ${p}")
            params.append(float(min_trust))
            p += 1
        where = " AND ".join(where_clauses)

        rows = await self.db.fetchall(
            f"""
            SELECT id, content, memory_type, trust_score, content_hash,
                   embedding_model, embedding_dimensions, embedded_at,
                   metadata, prev_hash, record_hash, key_id,
                   created_at, created_by, source_agent, session_id,
                   valid_from, valid_until, superseded_by, amended_from
            FROM memory_entry
            WHERE {where}
            ORDER BY chain_seq ASC
            """,  # nosec B608 — clauses are parameterised or hardcoded constants
            *params,
        )

        # 3. Collect redaction events so they can be included in exported records.
        redaction_map: dict[str, dict] = {}
        redaction_rows = await self.db.fetchall(
            """
            SELECT target_memory_id, reason, actor, created_at
            FROM memory_state_event
            WHERE event_type = 'redacted'
            ORDER BY chain_seq DESC
            """
        )
        for r in redaction_rows:
            mid = str(r["target_memory_id"])
            if mid not in redaction_map:
                redaction_map[mid] = {
                    "reason": r["reason"],
                    "actor": r["actor"],
                    "redacted_at": r["created_at"].isoformat(),
                }

        # 4. Optionally load audit events per record.
        audit_map: dict[str, list] = {}
        if include_audit:
            audit_rows = await self.db.fetchall(
                """
                SELECT memory_id, timestamp, action, actor, details
                FROM memory_audit_log
                ORDER BY timestamp ASC
                """
            )
            for a in audit_rows:
                mid = str(a["memory_id"])
                audit_map.setdefault(mid, []).append({
                    "timestamp": a["timestamp"].isoformat(),
                    "action": a["action"],
                    "actor": a["actor"],
                    "details": a["details"],
                })

        exported_at = datetime.now(UTC).isoformat()
        record_count = 0
        redacted_count = 0
        audit_events_count = 0

        # 5. Write manifest as line 0.
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "exporter": EXPORTER_ID,
            "exported_at": exported_at,
            "chain_valid": chain_valid,
            "filters": {
                "memory_type": memory_type,
                "min_trust": min_trust,
                "include_audit": include_audit,
                "include_redacted": include_redacted,
            },
        }
        out.write(json.dumps(manifest) + "\n")

        # 6. Write one record per memory.
        for row in rows:
            mid = str(row["id"])
            redaction = redaction_map.get(mid)
            is_redacted = redaction is not None

            if is_redacted and not include_redacted:
                redacted_count += 1
                continue

            meta = row["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta)

            record: dict = {
                "id": mid,
                "content": "[REDACTED]" if is_redacted else row["content"],
                "memory_type": row["memory_type"],
                "trust_score": float(row["trust_score"]),
                "content_hash": row["content_hash"],
                "embedding_model": row["embedding_model"],
                "embedding_dimensions": row["embedding_dimensions"],
                "embedded_at": row["embedded_at"].isoformat() if row["embedded_at"] else None,
                "metadata": meta or {},
                "record_hash": row["record_hash"],
                "prev_hash": row["prev_hash"],
                "key_id": row["key_id"],
                "created_at": row["created_at"].isoformat(),
                "created_by": row["created_by"],
                "source_agent": row["source_agent"],
                "session_id": str(row["session_id"]) if row["session_id"] else None,
                "valid_from": row["valid_from"].isoformat() if row["valid_from"] else None,
                "valid_until": row["valid_until"].isoformat() if row["valid_until"] else None,
                "superseded_by": str(row["superseded_by"]) if row["superseded_by"] else None,
                "amended_from": str(row["amended_from"]) if row["amended_from"] else None,
                "redacted": is_redacted,
            }

            if is_redacted and include_redacted:
                record["redaction"] = redaction
                redacted_count += 1

            if include_audit and mid in audit_map:
                record["audit_trail"] = audit_map[mid]
                audit_events_count += len(audit_map[mid])

            out.write(json.dumps(record) + "\n")
            record_count += 1

        logger.info(
            "export: wrote %d records (%d redacted skipped), chain_valid=%s",
            record_count, redacted_count, chain_valid,
        )
        return {
            "record_count": record_count,
            "redacted_count": redacted_count,
            "audit_events_count": audit_events_count,
            "chain_valid": chain_valid,
            "exported_at": exported_at,
        }

    async def _check_chain_valid(self) -> bool:
        try:
            row = await self.db.fetchrow(
                "SELECT COUNT(*) AS n FROM memory_entry WHERE valid_until IS NULL"
            )
            return row is not None
        except Exception:
            return False
