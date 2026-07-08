"""MemoryImporter — ingest a jeli export archive into the local memory store.

Import semantics:
  - Each record is treated as a NEW capture (new IDs, new chain position).
  - memory_type and content_class are preserved; trust is clamped to an import
    ceiling (default 0.3) because an archive is untrusted input, and
    server-owned provenance/security metadata keys are stripped (GH #37).
  - Embeddings are recomputed at destination using the local embedding model.
  - Redacted records in the export are skipped (content is [REDACTED]).
  - Records whose content_hash doesn't match the content string are rejected
    (tamper detection on the archive itself).
  - The import actor is stamped as "jeli-import" with original provenance in
    metadata so the audit trail is transparent.
  - Duplicate content (same content_hash already present) is skipped, not
    duplicated.

The result is a sovereign copy of the memories, re-embedded for local retrieval,
chained into the local store with full provenance. Nothing is assumed about the
source chain key — it's a new chain at the destination.
"""

import hashlib
import json
import logging
from typing import IO

from ..database.pool import AsyncPostgresPool
from ..embedding.provider import EmbeddingProvider
from ..tools.memory_tools import SERVER_OWNED_METADATA_KEYS, MemoryTools

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
IMPORT_ACTOR = "jeli-import"

# A portable archive is untrusted input: its content_hash proves the record
# was not corrupted in transit, NOT that it is trustworthy (anyone can compute
# a SHA-256). So imported trust is clamped to this ceiling by default (GH #37),
# preventing a crafted archive from laundering attacker content to user-tier
# 1.0 and weaponizing the conflict resolver against genuine memories. A user
# doing a known-good local restore can raise the ceiling explicitly.
DEFAULT_IMPORT_TRUST_CEILING = 0.3


class ImportError(Exception):
    """Raised when an import archive is malformed or a record is rejected."""


class MemoryImporter:
    """Imports memories from a JSON-Lines archive produced by MemoryExporter."""

    def __init__(
        self,
        db: AsyncPostgresPool,
        embedder: EmbeddingProvider,
        chain_key: str,
        key_id: str = "k1",
        dry_run: bool = False,
        trust_ceiling: float = DEFAULT_IMPORT_TRUST_CEILING,
    ):
        self.db = db
        self.embedder = embedder
        self.chain_key = chain_key
        self.key_id = key_id
        self.dry_run = dry_run
        self.trust_ceiling = trust_ceiling
        self._tools = MemoryTools(
            db=db, embedder=embedder, chain_key=chain_key, key_id=key_id
        )

    async def import_stream(self, inp: IO[str]) -> dict:
        """Read JSON-Lines from *inp*, import valid records.

        Returns summary: {imported, skipped_redacted, skipped_duplicate,
        skipped_tampered, errors, dry_run}.
        """
        lines = inp.readlines()
        if not lines:
            raise ImportError("empty import archive")

        # Line 0 is the manifest.
        manifest = self._parse_manifest(lines[0])
        schema_ver = manifest.get("schema_version")
        if schema_ver != SCHEMA_VERSION:
            raise ImportError(
                f"unsupported schema_version '{schema_ver}' (expected '{SCHEMA_VERSION}')"
            )

        # Pre-load existing content_hashes so we can skip duplicates cheaply.
        existing_hashes: set[str] = set()
        if not self.dry_run:
            rows = await self.db.fetchall(
                "SELECT content_hash FROM memory_entry WHERE valid_until IS NULL"
            )
            existing_hashes = {r["content_hash"] for r in rows}

        imported = 0
        skipped_redacted = 0
        skipped_duplicate = 0
        skipped_tampered = 0
        errors = 0

        for lineno, line in enumerate(lines[1:], start=2):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("import: line %d malformed JSON: %s", lineno, exc)
                errors += 1
                continue

            result = await self._import_record(
                record, lineno, existing_hashes
            )
            if result == "imported":
                imported += 1
                if not self.dry_run and "content_hash" in record:
                    existing_hashes.add(record["content_hash"])
            elif result == "skipped_redacted":
                skipped_redacted += 1
            elif result == "skipped_duplicate":
                skipped_duplicate += 1
            elif result == "skipped_tampered":
                skipped_tampered += 1
            else:
                errors += 1

        logger.info(
            "import: imported=%d skipped_redacted=%d skipped_duplicate=%d "
            "skipped_tampered=%d errors=%d dry_run=%s",
            imported, skipped_redacted, skipped_duplicate, skipped_tampered,
            errors, self.dry_run,
        )
        return {
            "imported": imported,
            "skipped_redacted": skipped_redacted,
            "skipped_duplicate": skipped_duplicate,
            "skipped_tampered": skipped_tampered,
            "errors": errors,
            "dry_run": self.dry_run,
        }

    async def _import_record(
        self,
        record: dict,
        lineno: int,
        existing_hashes: set[str],
    ) -> str:
        content = record.get("content", "")
        memory_type = record.get("memory_type", "episodic")
        # Clamp untrusted archive trust to the import ceiling (GH #37).
        trust_score = min(float(record.get("trust_score", 0.5)), self.trust_ceiling)
        content_hash = record.get("content_hash", "")
        # Strip server-owned provenance/security keys a crafted archive could
        # use to spoof daemon output or downgrade the injection wrap (GH #37,
        # shares the whitelist with the MCP boundary in GH #35).
        meta = {
            k: v
            for k, v in (record.get("metadata") or {}).items()
            if k not in SERVER_OWNED_METADATA_KEYS
        }
        redacted = record.get("redacted", False)

        # Skip redacted records — content is "[REDACTED]", unusable.
        if redacted or content == "[REDACTED]":
            logger.debug("import: line %d skipped (redacted)", lineno)
            return "skipped_redacted"

        # Tamper-check: recompute SHA-256 of content vs stored hash.
        if content_hash:
            computed = hashlib.sha256(content.encode()).hexdigest()
            if computed != content_hash:
                logger.warning(
                    "import: line %d rejected — content_hash mismatch "
                    "(archive may be tampered)", lineno
                )
                return "skipped_tampered"

        # Skip exact duplicates (same content already in local store).
        if content_hash in existing_hashes:
            logger.debug("import: line %d skipped (duplicate content_hash)", lineno)
            return "skipped_duplicate"

        # Stamp provenance into metadata.
        meta["imported_from"] = {
            "original_id": record.get("id"),
            "original_created_at": record.get("created_at"),
            "original_created_by": record.get("created_by"),
            "original_source_agent": record.get("source_agent"),
            "import_actor": IMPORT_ACTOR,
        }
        content_class = meta.pop("content_class", "general")

        if self.dry_run:
            logger.debug("import: line %d would import (dry_run)", lineno)
            return "imported"

        try:
            await self._tools.capture_memory(
                content=content,
                memory_type=memory_type,
                trust_score=trust_score,
                actor=IMPORT_ACTOR,
                source_agent=record.get("source_agent") or IMPORT_ACTOR,
                session_id=None,
                metadata=meta,
                content_class=content_class,
            )
        except Exception as exc:
            logger.warning("import: line %d capture failed: %s", lineno, exc)
            return "error"

        return "imported"

    @staticmethod
    def _parse_manifest(line: str) -> dict:
        line = line.strip()
        if not line:
            raise ImportError("manifest line is empty")
        try:
            manifest: dict = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ImportError(f"manifest is not valid JSON: {exc}") from exc
        return manifest
