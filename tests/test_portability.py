"""Tests for memory export/import portability layer."""

import hashlib
import io
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jeli_scoped_mcp.core.hash_chain import build_canonical_record, compute_record_hash
from jeli_scoped_mcp.portability.exporter import MemoryExporter
from jeli_scoped_mcp.portability.importer import ImportError, MemoryImporter

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_row(content: str = "hello world", memory_type: str = "semantic",
              trust_score: float = 0.8, redacted: bool = False) -> dict:
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    return {
        "id": uuid.uuid4(),
        "content": content,
        "memory_type": memory_type,
        "trust_score": trust_score,
        "content_hash": content_hash,
        "embedding_model": "test/embed",
        "embedding_dimensions": 1024,
        "embedded_at": datetime.now(UTC),
        "metadata": {"content_class": "general"},
        "prev_hash": None,
        "record_hash": "abc123",
        "key_id": "k1",
        "created_at": datetime.now(UTC),
        "created_by": "test-actor",
        "source_agent": "test-agent",
        "session_id": None,
        "valid_from": datetime.now(UTC),
        "valid_until": None,
        "superseded_by": None,
        "amended_from": None,
    }


def _make_db(rows: list[dict], redaction_rows: list | None = None) -> MagicMock:
    db = MagicMock()
    audit_rows: list = []

    async def fetchall(query, *args):
        q = query.strip()
        if "constitutional_rules" in q:
            return []
        if "memory_entry" in q:
            return rows
        if "memory_state_event" in q:
            return redaction_rows or []
        if "memory_audit_log" in q:
            return audit_rows
        return []

    async def fetchrow(query, *args):
        if "COUNT" in query:
            return {"n": len(rows)}
        return None

    db.fetchall = fetchall
    db.fetchrow = fetchrow
    return db


# ── MemoryExporter ────────────────────────────────────────────────────────────


class TestMemoryExporter:
    @pytest.mark.asyncio
    async def test_export_manifest_is_first_line(self):
        db = _make_db([_make_row()])
        out = io.StringIO()
        await MemoryExporter(db=db).export(out)
        lines = out.getvalue().strip().split("\n")
        manifest = json.loads(lines[0])
        assert manifest["schema_version"] == "1.0"
        assert manifest["exporter"] == "jeli-scoped-mcp"
        assert "exported_at" in manifest
        assert "chain_valid" in manifest

    @pytest.mark.asyncio
    async def test_export_record_count(self):
        db = _make_db([_make_row("a"), _make_row("b"), _make_row("c")])
        out = io.StringIO()
        result = await MemoryExporter(db=db).export(out)
        assert result["record_count"] == 3

    @pytest.mark.asyncio
    async def test_export_no_embeddings_in_output(self):
        db = _make_db([_make_row("check for vectors")])
        out = io.StringIO()
        await MemoryExporter(db=db).export(out)
        lines = out.getvalue().strip().split("\n")
        record = json.loads(lines[1])
        assert "embedding" not in record  # no raw float list
        # embedding_model and embedding_dimensions fields are metadata, not vectors
        assert not any(isinstance(v, list) for v in record.values())

    @pytest.mark.asyncio
    async def test_export_redacted_skipped_by_default(self):
        row = _make_row()
        redaction = {
            "target_memory_id": row["id"],
            "reason": "user request",
            "actor": "jp-cruz",
            "created_at": datetime.now(UTC),
        }
        db = _make_db([row], redaction_rows=[redaction])
        out = io.StringIO()
        result = await MemoryExporter(db=db).export(out, include_redacted=False)
        # Redacted record is skipped
        assert result["redacted_count"] == 1
        lines = [ln for ln in out.getvalue().strip().split("\n") if ln]
        assert len(lines) == 1  # only manifest

    @pytest.mark.asyncio
    async def test_export_redacted_included_when_flag_set(self):
        row = _make_row()
        redaction = {
            "target_memory_id": row["id"],
            "reason": "user request",
            "actor": "jp-cruz",
            "created_at": datetime.now(UTC),
        }
        db = _make_db([row], redaction_rows=[redaction])
        out = io.StringIO()
        result = await MemoryExporter(db=db).export(out, include_redacted=True)
        assert result["redacted_count"] == 1
        lines = [ln for ln in out.getvalue().strip().split("\n") if ln]
        assert len(lines) == 2  # manifest + 1 record
        record = json.loads(lines[1])
        assert record["content"] == "[REDACTED]"
        assert record["redacted"] is True

    @pytest.mark.asyncio
    async def test_export_content_hash_preserved(self):
        content = "JP prefers dark roast coffee."
        row = _make_row(content=content)
        db = _make_db([row])
        out = io.StringIO()
        await MemoryExporter(db=db).export(out)
        lines = out.getvalue().strip().split("\n")
        record = json.loads(lines[1])
        assert record["content_hash"] == hashlib.sha256(content.encode()).hexdigest()

    @pytest.mark.asyncio
    async def test_export_with_audit_includes_events(self):
        row = _make_row()
        db = _make_db([row])
        # Patch audit_map via fetchall to return events
        original_fetchall = db.fetchall

        async def patched_fetchall(query, *args):
            if "memory_audit_log" in query:
                return [{
                    "memory_id": row["id"],
                    "timestamp": datetime.now(UTC),
                    "action": "created",
                    "actor": "jp-cruz",
                    "details": {"trust_score": 0.8},
                }]
            return await original_fetchall(query, *args)

        db.fetchall = patched_fetchall
        out = io.StringIO()
        result = await MemoryExporter(db=db).export(out, include_audit=True)
        lines = out.getvalue().strip().split("\n")
        record = json.loads(lines[1])
        assert "audit_trail" in record
        assert result["audit_events_count"] == 1


# ── MemoryImporter ────────────────────────────────────────────────────────────


def _make_export_stream(records: list[dict], chain_valid: bool = True) -> io.StringIO:
    """Build a JSON-Lines stream the importer can consume."""
    manifest = {
        "schema_version": "1.0",
        "exporter": "jeli-scoped-mcp",
        "exported_at": datetime.now(UTC).isoformat(),
        "chain_valid": chain_valid,
        "filters": {},
    }
    lines = [json.dumps(manifest)]
    for r in records:
        lines.append(json.dumps(r))
    return io.StringIO("\n".join(lines) + "\n")


def _record_dict(content: str = "some memory", memory_type: str = "semantic",
                 trust_score: float = 0.7, redacted: bool = False) -> dict:
    ch = hashlib.sha256(content.encode()).hexdigest()
    return {
        "id": str(uuid.uuid4()),
        "content": "[REDACTED]" if redacted else content,
        "memory_type": memory_type,
        "trust_score": trust_score,
        "content_hash": ch,
        "metadata": {"content_class": "general"},
        "redacted": redacted,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": "original-actor",
        "source_agent": "original-agent",
    }


class TestMemoryImporter:
    def _make_importer(self, existing: list[str] | None = None) -> tuple:
        db = MagicMock()

        async def fetchall(query, *args):
            if "content_hash" in query:
                return [{"content_hash": h} for h in (existing or [])]
            if "constitutional_rules" in query:
                return []
            return []

        async def fetchrow(query, *args):
            return None

        db.fetchall = fetchall
        db.fetchrow = fetchrow

        embedder = MagicMock()

        with patch("jeli_scoped_mcp.portability.importer.MemoryTools") as mock_tools_cls:
            mock_tools = MagicMock()
            mock_tools.capture_memory = AsyncMock(return_value={"id": str(uuid.uuid4()),
                                                                "trust_score": 0.7,
                                                                "record_hash": "x"})
            mock_tools_cls.return_value = mock_tools
            importer = MemoryImporter(
                db=db, embedder=embedder, chain_key="test-key", key_id="k1"
            )
            importer._tools = mock_tools
        return importer, mock_tools

    @pytest.mark.asyncio
    async def test_import_happy_path(self):
        importer, mock_tools = self._make_importer()
        stream = _make_export_stream([_record_dict("memory one"), _record_dict("memory two")])
        result = await importer.import_stream(stream)
        assert result["imported"] == 2
        assert result["errors"] == 0
        assert mock_tools.capture_memory.call_count == 2

    @pytest.mark.asyncio
    async def test_import_skips_redacted(self):
        importer, mock_tools = self._make_importer()
        stream = _make_export_stream([_record_dict(redacted=True), _record_dict("normal")])
        result = await importer.import_stream(stream)
        assert result["imported"] == 1
        assert result["skipped_redacted"] == 1

    @pytest.mark.asyncio
    async def test_import_skips_duplicate_content_hash(self):
        content = "already here"
        existing_hash = hashlib.sha256(content.encode()).hexdigest()
        importer, mock_tools = self._make_importer(existing=[existing_hash])
        stream = _make_export_stream([_record_dict(content)])
        result = await importer.import_stream(stream)
        assert result["skipped_duplicate"] == 1
        assert result["imported"] == 0

    @pytest.mark.asyncio
    async def test_import_rejects_tampered_content(self):
        importer, mock_tools = self._make_importer()
        record = _record_dict("original content")
        record["content"] = "tampered content"  # hash no longer matches
        stream = _make_export_stream([record])
        result = await importer.import_stream(stream)
        assert result["skipped_tampered"] == 1
        assert result["imported"] == 0

    @pytest.mark.asyncio
    async def test_import_stamps_provenance_in_metadata(self):
        importer, mock_tools = self._make_importer()
        rec = _record_dict("some content", trust_score=0.6)
        rec["created_by"] = "original-person"
        stream = _make_export_stream([rec])
        await importer.import_stream(stream)
        call_kwargs = mock_tools.capture_memory.call_args.kwargs
        meta = call_kwargs["metadata"]
        assert "imported_from" in meta
        assert meta["imported_from"]["original_created_by"] == "original-person"
        assert meta["imported_from"]["import_actor"] == "jeli-import"

    @pytest.mark.asyncio
    async def test_import_clamps_trust_to_ceiling_by_default(self):
        """GH #37: an archive's trust is clamped to the import ceiling (0.3)."""
        importer, mock_tools = self._make_importer()
        stream = _make_export_stream([_record_dict(trust_score=0.9)])
        await importer.import_stream(stream)
        call_kwargs = mock_tools.capture_memory.call_args.kwargs
        assert call_kwargs["trust_score"] == 0.3

    async def test_import_trust_ceiling_override_preserves_up_to_ceiling(self):
        """A known-good local restore can raise the ceiling explicitly."""
        importer, mock_tools = self._make_importer()
        importer.trust_ceiling = 1.0
        stream = _make_export_stream([_record_dict(trust_score=0.9)])
        await importer.import_stream(stream)
        assert mock_tools.capture_memory.call_args.kwargs["trust_score"] == 0.9

    async def test_import_strips_server_owned_metadata(self):
        """GH #37: a crafted archive can't smuggle server-owned provenance keys."""
        importer, mock_tools = self._make_importer()
        rec = _record_dict(trust_score=0.3)
        rec["metadata"] = {
            "injection_flagged": False,
            "insight_type": "cluster",
            "trust_override_reason": "spoof",
            "project": "keep",
        }
        await importer.import_stream(_make_export_stream([rec]))
        meta = mock_tools.capture_memory.call_args.kwargs["metadata"]
        assert "injection_flagged" not in meta
        assert "insight_type" not in meta
        assert "trust_override_reason" not in meta
        assert meta["project"] == "keep"

    @pytest.mark.asyncio
    async def test_import_dry_run_does_not_write(self):
        db = MagicMock()

        async def fetchall(q, *a):
            return []

        async def fetchrow(q, *a):
            return None

        db.fetchall = fetchall
        db.fetchrow = fetchrow
        embedder = MagicMock()

        with patch("jeli_scoped_mcp.portability.importer.MemoryTools") as mock_tools_cls:
            mock_tools = MagicMock()
            mock_tools.capture_memory = AsyncMock()
            mock_tools_cls.return_value = mock_tools
            importer = MemoryImporter(
                db=db, embedder=embedder, chain_key="k", key_id="k1", dry_run=True
            )
            importer._tools = mock_tools

        stream = _make_export_stream([_record_dict("memory"), _record_dict("another")])
        result = await importer.import_stream(stream)
        assert result["imported"] == 2  # counted as would-import
        assert result["dry_run"] is True
        mock_tools.capture_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_import_empty_stream_raises(self):
        importer, _ = self._make_importer()
        with pytest.raises(ImportError, match="empty"):
            await importer.import_stream(io.StringIO(""))

    @pytest.mark.asyncio
    async def test_import_bad_schema_version_raises(self):
        importer, _ = self._make_importer()
        manifest = {"schema_version": "99.0", "exporter": "jeli-scoped-mcp",
                    "exported_at": "now", "chain_valid": True, "filters": {}}
        stream = io.StringIO(json.dumps(manifest) + "\n")
        with pytest.raises(ImportError, match="schema_version"):
            await importer.import_stream(stream)

    @pytest.mark.asyncio
    async def test_import_skips_malformed_json_lines(self):
        importer, mock_tools = self._make_importer()
        manifest = {"schema_version": "1.0", "exporter": "jeli-scoped-mcp",
                    "exported_at": "now", "chain_valid": True, "filters": {}}
        stream = io.StringIO(
            json.dumps(manifest) + "\n"
            + "not valid json at all\n"
            + json.dumps(_record_dict("good")) + "\n"
        )
        result = await importer.import_stream(stream)
        assert result["errors"] == 1
        assert result["imported"] == 1


# ── importer branch coverage ─────────────────────────────────────────────────


class TestImporterBranches:
    def _make_importer(self):
        db = MagicMock()

        async def fetchall(query, *args):
            return []

        async def fetchrow(query, *args):
            return None

        db.fetchall = fetchall
        db.fetchrow = fetchrow

        with patch("jeli_scoped_mcp.portability.importer.MemoryTools") as mock_cls:
            mock_tools = MagicMock()
            mock_tools.capture_memory = AsyncMock(return_value={"id": str(uuid.uuid4()),
                                                                "trust_score": 0.7,
                                                                "record_hash": "x"})
            mock_cls.return_value = mock_tools
            importer = MemoryImporter(db=db, embedder=MagicMock(), chain_key="k", key_id="k1")
            importer._tools = mock_tools
        return importer, mock_tools

    @pytest.mark.asyncio
    async def test_import_empty_body_lines_skipped(self):
        """Blank lines between records are skipped without incrementing any counter."""
        importer, mock_tools = self._make_importer()
        manifest = {"schema_version": "1.0", "exporter": "jeli-scoped-mcp",
                    "exported_at": "now", "chain_valid": True, "filters": {}}
        rec = _record_dict("hello world")
        stream = io.StringIO(
            json.dumps(manifest) + "\n"
            + "\n"        # empty line — must be skipped cleanly
            + "   \n"     # whitespace-only line — same
            + json.dumps(rec) + "\n"
        )
        result = await importer.import_stream(stream)
        assert result["imported"] == 1
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_import_capture_failure_increments_errors(self):
        """capture_memory raising → result='error' → else branch → errors += 1."""
        importer, mock_tools = self._make_importer()
        mock_tools.capture_memory = AsyncMock(side_effect=RuntimeError("DB down"))
        manifest = {"schema_version": "1.0", "exporter": "jeli-scoped-mcp",
                    "exported_at": "now", "chain_valid": True, "filters": {}}
        stream = io.StringIO(
            json.dumps(manifest) + "\n"
            + json.dumps(_record_dict("content that will fail")) + "\n"
        )
        result = await importer.import_stream(stream)
        assert result["errors"] == 1
        assert result["imported"] == 0

    def test_parse_manifest_empty_string_raises(self):
        from jeli_scoped_mcp.portability.importer import MemoryImporter as MI
        with pytest.raises(ImportError, match="empty"):
            MI._parse_manifest("")

    def test_parse_manifest_invalid_json_raises(self):
        from jeli_scoped_mcp.portability.importer import MemoryImporter as MI
        with pytest.raises(ImportError, match="not valid JSON"):
            MI._parse_manifest("{bad json{{")


class TestExporterBranches:
    @pytest.mark.asyncio
    async def test_export_with_memory_type_filter(self):
        """memory_type kwarg appends a WHERE clause and param — covers lines 57-59."""
        rows = [_make_row("semantic fact", memory_type="semantic")]
        db = _make_db(rows)
        out = io.StringIO()
        result = await MemoryExporter(db=db).export(out, memory_type="semantic")
        assert result["record_count"] == 1

    @pytest.mark.asyncio
    async def test_export_with_min_trust_filter(self):
        """min_trust kwarg appends a WHERE clause and param — covers lines 61-63."""
        rows = [_make_row("high trust", trust_score=0.9)]
        db = _make_db(rows)
        out = io.StringIO()
        result = await MemoryExporter(db=db).export(out, min_trust=0.7)
        assert result["record_count"] == 1

    @pytest.mark.asyncio
    async def test_export_metadata_as_string_parsed(self):
        """metadata stored as a JSON string (older asyncpg) is decoded on export."""
        row = _make_row("meta as string")
        row["metadata"] = json.dumps({"content_class": "general"})  # string, not dict
        db = _make_db([row])
        out = io.StringIO()
        await MemoryExporter(db=db).export(out)
        lines = out.getvalue().strip().split("\n")
        record = json.loads(lines[1])
        assert record["metadata"] == {"content_class": "general"}

    @pytest.mark.asyncio
    async def test_check_chain_valid_db_error_returns_false(self):
        """DB error in _check_chain_valid → returns False (chain_valid = False)."""
        db = MagicMock()

        async def fetchrow_raises(*a, **kw):
            raise RuntimeError("DB connection lost")

        async def fetchall(*a, **kw):
            return []

        db.fetchrow = fetchrow_raises
        db.fetchall = fetchall
        out = io.StringIO()
        result = await MemoryExporter(db=db).export(out)
        assert result["chain_valid"] is False


# ── GH #41: HMAC-verified import (trust preservation) ────────────────────────


def _make_hmac_verified_record(
    content: str,
    chain_key: str,
    key_id: str = "k1",
    trust_score: float = 0.8,
    metadata: dict | None = None,
    prev_hash: str | None = None,
) -> dict:
    """Build an export record whose record_hash was signed by chain_key."""
    meta = metadata if metadata is not None else {"content_class": "general"}
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    canonical = build_canonical_record(
        content=content,
        embedding_model="test/embed",
        embedding_dimensions=1024,
        trust_score=trust_score,
        memory_type="semantic",
        key_id=key_id,
        metadata=meta if meta else None,
    )
    record_hash = compute_record_hash(chain_key, canonical, prev_hash)
    return {
        "id": str(uuid.uuid4()),
        "content": content,
        "memory_type": "semantic",
        "trust_score": trust_score,
        "content_hash": content_hash,
        "embedding_model": "test/embed",
        "embedding_dimensions": 1024,
        "metadata": meta,
        "record_hash": record_hash,
        "prev_hash": prev_hash,
        "key_id": key_id,
        "redacted": False,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": "test-actor",
        "source_agent": "test-agent",
    }


class TestHmacVerifiedImport:
    """GH #41 — HMAC-verified import preserves trust; unverified stays clamped."""

    def _make_importer(self, chain_key: str = "test-chain-key", key_id: str = "k1"):
        db = MagicMock()

        async def fetchall(query, *args):
            if "content_hash" in query:
                return []
            if "constitutional_rules" in query:
                return []
            return []

        async def fetchrow(query, *args):
            return None

        db.fetchall = fetchall
        db.fetchrow = fetchrow

        with patch("jeli_scoped_mcp.portability.importer.MemoryTools") as mock_cls:
            mock_tools = MagicMock()
            mock_tools.capture_memory = AsyncMock(
                return_value={"id": str(uuid.uuid4()), "trust_score": 0.8, "record_hash": "x"}
            )
            mock_cls.return_value = mock_tools
            importer = MemoryImporter(
                db=db, embedder=MagicMock(), chain_key=chain_key, key_id=key_id
            )
            importer._tools = mock_tools
        return importer, mock_tools

    @pytest.mark.asyncio
    async def test_verified_record_preserves_original_trust(self):
        """A record signed by the local chain key is imported at original trust (no ceiling)."""
        chain_key = "test-chain-key"
        rec = _make_hmac_verified_record("verified memory", chain_key, trust_score=0.9)
        importer, mock_tools = self._make_importer(chain_key=chain_key)
        await importer.import_stream(_make_export_stream([rec]))
        trust = mock_tools.capture_memory.call_args.kwargs["trust_score"]
        assert trust == 0.9

    @pytest.mark.asyncio
    async def test_unverified_record_still_clamped_to_ceiling(self):
        """A record with wrong chain key is still imported but trust is clamped."""
        rec = _make_hmac_verified_record("foreign memory", "other-chain-key", trust_score=0.9)
        importer, mock_tools = self._make_importer(chain_key="test-chain-key")
        await importer.import_stream(_make_export_stream([rec]))
        trust = mock_tools.capture_memory.call_args.kwargs["trust_score"]
        assert trust == 0.3  # default ceiling

    @pytest.mark.asyncio
    async def test_missing_record_hash_falls_back_to_ceiling(self):
        """Records without record_hash (older exports) fall back to trust ceiling."""
        rec = _record_dict("no hash record", trust_score=0.8)
        importer, mock_tools = self._make_importer()
        await importer.import_stream(_make_export_stream([rec]))
        trust = mock_tools.capture_memory.call_args.kwargs["trust_score"]
        assert trust == 0.3

    @pytest.mark.asyncio
    async def test_wrong_key_id_falls_back_to_ceiling(self):
        """key_id mismatch skips HMAC check entirely and applies ceiling."""
        rec = _make_hmac_verified_record("memory", "test-chain-key", key_id="k99", trust_score=0.9)
        importer, mock_tools = self._make_importer(chain_key="test-chain-key", key_id="k1")
        await importer.import_stream(_make_export_stream([rec]))
        trust = mock_tools.capture_memory.call_args.kwargs["trust_score"]
        assert trust == 0.3

    @pytest.mark.asyncio
    async def test_tampered_hash_falls_back_to_ceiling(self):
        """A record whose record_hash was altered after signing fails verify → ceiling."""
        rec = _make_hmac_verified_record("memory", "test-chain-key", trust_score=0.9)
        rec["record_hash"] = "deadbeef" * 8  # corrupt the hash
        importer, mock_tools = self._make_importer(chain_key="test-chain-key")
        await importer.import_stream(_make_export_stream([rec]))
        trust = mock_tools.capture_memory.call_args.kwargs["trust_score"]
        assert trust == 0.3

    @pytest.mark.asyncio
    async def test_verified_record_stamps_hmac_verified_in_metadata(self):
        """Verified records carry import_hmac_verified: True in imported_from metadata."""
        chain_key = "test-chain-key"
        rec = _make_hmac_verified_record("stamped memory", chain_key, trust_score=0.7)
        importer, mock_tools = self._make_importer(chain_key=chain_key)
        await importer.import_stream(_make_export_stream([rec]))
        meta = mock_tools.capture_memory.call_args.kwargs["metadata"]
        assert meta["imported_from"]["hmac_verified"] is True

    @pytest.mark.asyncio
    async def test_unverified_record_stamps_hmac_verified_false(self):
        """Unverified records carry import_hmac_verified: False in imported_from metadata."""
        rec = _record_dict("unverified memory", trust_score=0.7)
        importer, mock_tools = self._make_importer()
        await importer.import_stream(_make_export_stream([rec]))
        meta = mock_tools.capture_memory.call_args.kwargs["metadata"]
        assert meta["imported_from"]["hmac_verified"] is False

    @pytest.mark.asyncio
    async def test_verified_record_still_strips_nonprotective_server_metadata(self):
        """Verified origin does not replay nonprotective server authority."""
        chain_key = "test-chain-key"
        dangerous_meta = {
            "content_class": "general",
            "injection_flagged": False,
            "trust_override_reason": "spoof",
        }
        rec = _make_hmac_verified_record(
            "spoofed memory", chain_key, trust_score=0.9, metadata=dangerous_meta
        )
        importer, mock_tools = self._make_importer(chain_key=chain_key)
        await importer.import_stream(_make_export_stream([rec]))
        meta = mock_tools.capture_memory.call_args.kwargs["metadata"]
        assert "injection_flagged" not in meta
        assert "trust_override_reason" not in meta

    @pytest.mark.asyncio
    async def test_verified_record_preserves_positive_injection_flags(self):
        """GH #53: attested quarantine state survives an own-store restore."""
        chain_key = "test-chain-key"
        rec = _make_hmac_verified_record(
            "natural-language injection",
            chain_key,
            metadata={
                "content_class": "general",
                "injection_flagged": True,
                "llm_injection_flagged": True,
                "trust_override_reason": "must-still-be-stripped",
            },
        )
        importer, mock_tools = self._make_importer(chain_key=chain_key)
        await importer.import_stream(_make_export_stream([rec]))

        meta = mock_tools.capture_memory.call_args.kwargs["metadata"]
        assert meta["injection_flagged"] is True
        assert meta["llm_injection_flagged"] is True
        assert "trust_override_reason" not in meta

    @pytest.mark.asyncio
    async def test_verified_llm_flag_implies_aggregate_injection_flag(self):
        """An inconsistent old record cannot restore an unwrapped LLM flag."""
        chain_key = "test-chain-key"
        rec = _make_hmac_verified_record(
            "natural-language injection",
            chain_key,
            metadata={"content_class": "general", "llm_injection_flagged": True},
        )
        importer, mock_tools = self._make_importer(chain_key=chain_key)
        await importer.import_stream(_make_export_stream([rec]))

        meta = mock_tools.capture_memory.call_args.kwargs["metadata"]
        assert meta["llm_injection_flagged"] is True
        assert meta["injection_flagged"] is True

    @pytest.mark.asyncio
    async def test_unverified_record_cannot_preserve_positive_injection_flags(self):
        """Foreign archives still cannot assert server-owned security state."""
        rec = _make_hmac_verified_record(
            "foreign payload",
            "foreign-chain-key",
            metadata={
                "content_class": "general",
                "injection_flagged": True,
                "llm_injection_flagged": True,
            },
        )
        importer, mock_tools = self._make_importer(chain_key="test-chain-key")
        await importer.import_stream(_make_export_stream([rec]))

        meta = mock_tools.capture_memory.call_args.kwargs["metadata"]
        assert "injection_flagged" not in meta
        assert "llm_injection_flagged" not in meta

    def test_try_verify_hmac_swallows_exceptions(self):
        """_try_verify_hmac never raises — bad embedding_dimensions etc. → False."""
        importer, _ = self._make_importer()
        record = {"record_hash": "x", "key_id": "k1", "embedding_dimensions": "not-an-int"}
        assert importer._try_verify_hmac(record, {}) is False


# ── round-trip sanity ─────────────────────────────────────────────────────────


class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_export_then_import_count_matches(self):
        """Export N records → import stream → N would-import (dry_run)."""
        records = [_make_row(f"memory {i}") for i in range(5)]
        db_export = _make_db(records)
        out = io.StringIO()
        export_result = await MemoryExporter(db=db_export).export(out)
        assert export_result["record_count"] == 5

        out.seek(0)

        db_import = MagicMock()

        async def fetchall_import(q, *a):
            return []

        async def fetchrow_import(q, *a):
            return None

        db_import.fetchall = fetchall_import
        db_import.fetchrow = fetchrow_import

        with patch("jeli_scoped_mcp.portability.importer.MemoryTools") as mock_cls:
            mock_tools = MagicMock()
            mock_tools.capture_memory = AsyncMock(return_value={
                "id": str(uuid.uuid4()), "trust_score": 0.8, "record_hash": "x"
            })
            mock_cls.return_value = mock_tools
            importer = MemoryImporter(
                db=db_import, embedder=MagicMock(),
                chain_key="k", key_id="k1", dry_run=True
            )
            importer._tools = mock_tools
            import_result = await importer.import_stream(out)

        assert import_result["imported"] == 5
        assert import_result["skipped_tampered"] == 0
