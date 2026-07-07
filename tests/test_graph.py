"""Tests for entity graph layer: extractor, store, and capture integration."""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jeli_scoped_mcp.graph.extractor import EntityExtractor
from jeli_scoped_mcp.graph.store import GraphStore

# ── EntityExtractor ───────────────────────────────────────────────────────────


class TestEntityExtractor:
    def setup_method(self):
        self.ex = EntityExtractor()

    def test_extract_person_name(self):
        results = self.ex.extract("JP Cruz worked on the project.")
        names = [r["name"] for r in results]
        assert "JP Cruz" in names
        person = next(r for r in results if r["name"] == "JP Cruz")
        assert person["entity_type"] == "person"

    def test_extract_technology_from_gazetteer(self):
        results = self.ex.extract("We use PostgreSQL and pgvector.")
        types = {r["name"]: r["entity_type"] for r in results}
        assert types.get("PostgreSQL") == "technology"
        assert types.get("pgvector") == "technology"

    def test_extract_project_from_gazetteer(self):
        results = self.ex.extract("Jeli is a memory sovereignty layer.")
        types = {r["name"]: r["entity_type"] for r in results}
        assert types.get("Jeli") == "project"

    def test_extract_organization_from_gazetteer(self):
        results = self.ex.extract("LegionForge publishes the package.")
        types = {r["name"]: r["entity_type"] for r in results}
        assert types.get("LegionForge") == "organization"

    def test_extract_url_as_organization(self):
        results = self.ex.extract("See https://github.com/LegionForge for details.")
        names = [r["name"] for r in results]
        assert any(n == "github.com" for n in names)
        org = next(r for r in results if r["name"] == "github.com")
        assert org["entity_type"] == "organization"

    def test_extract_url_strips_www(self):
        results = self.ex.extract("Visit https://www.example.com today.")
        names = [r["name"] for r in results]
        assert any(n == "example.com" for n in names)
        assert all(n != "www.example.com" for n in names)

    def test_no_false_positives_on_lowercase(self):
        results = self.ex.extract("the quick brown fox jumped over the lazy dog.")
        assert results == []

    def test_empty_content_returns_empty(self):
        assert self.ex.extract("") == []
        assert self.ex.extract("   ") == []

    def test_deduplication(self):
        results = self.ex.extract("JP Cruz and JP Cruz again.")
        person_results = [r for r in results if r["name"] == "JP Cruz"]
        assert len(person_results) == 1

    def test_gazetteer_takes_precedence_over_person_heuristic(self):
        results = self.ex.extract("Jeli is a project by JP Cruz.")
        jeli_results = [r for r in results if r["name"] == "Jeli"]
        assert len(jeli_results) == 1
        assert jeli_results[0]["entity_type"] == "project"

    def test_extra_keywords_extend_gazetteer(self):
        ex = EntityExtractor(extra_keywords={"Briarios": "project"})
        results = ex.extract("Briarios is the action gate.")
        names = {r["name"]: r["entity_type"] for r in results}
        assert names.get("Briarios") == "project"

    def test_confidence_values_in_range(self):
        results = self.ex.extract("JP Cruz uses PostgreSQL at github.com.")
        for r in results:
            assert 0.0 <= r["confidence"] <= 1.0

    def test_extract_relations_person_works_on_project(self):
        entities = self.ex.extract("JP Cruz works on Jeli.")
        relations = self.ex.extract_relations(entities)
        triples = {(s, p, o) for s, p, o, _ in relations}
        assert ("JP Cruz", "works_on", "Jeli") in triples

    def test_extract_relations_empty_when_no_co_occurrence(self):
        # Only persons, no projects/orgs/techs → nothing to relate them to.
        entities = [{"name": "JP Cruz", "entity_type": "person", "confidence": 0.7}]
        assert self.ex.extract_relations(entities) == []

    def test_extract_relations_capped_at_20(self):
        entities = (
            [{"name": f"P{i}", "entity_type": "person", "confidence": 0.7} for i in range(10)]
            + [{"name": f"J{i}", "entity_type": "project", "confidence": 0.9} for i in range(10)]
        )
        relations = self.ex.extract_relations(entities)
        assert len(relations) <= 20

    def test_extract_email_domain_as_organization(self):
        """Email addresses yield the domain as an organization entity."""
        results = self.ex.extract("Contact support@example.org for help.")
        names = [r["name"] for r in results]
        assert any(n == "example.org" for n in names)
        org = next(r for r in results if r["name"] == "example.org")
        assert org["entity_type"] == "organization"
        assert org["confidence"] == pytest.approx(0.7)

    def test_extract_person_token_overlaps_gazetteer_skipped(self):
        """'Docker User' — 'Docker' is in the gazetteer, so the person match is skipped."""
        results = self.ex.extract("Docker User deployed the stack.")
        names = [r["name"] for r in results]
        assert "Docker User" not in names
        # The gazetteer still picks up Docker as a technology.
        assert "Docker" in names

    def test_extract_exact_phrase_in_keyword_names_skipped(self):
        """A person-regex match whose full lowercased form is in keyword_names is skipped."""
        # Add "Nate Jones" as a keyword so it appears in keyword_names.
        ex = EntityExtractor(extra_keywords={"Nate Jones": "person"})
        results = ex.extract("Nate Jones attended the meeting.")
        nate = [r for r in results if r["name"] == "Nate Jones"]
        # Gazetteer wins (confidence 0.9); the person-heuristic path is skipped.
        assert len(nate) == 1
        assert nate[0]["confidence"] == pytest.approx(0.9)

    def test_extract_relations_person_member_of_org(self):
        """person + organization → member_of edge."""
        entities = [
            {"name": "JP Cruz", "entity_type": "person", "confidence": 0.7},
            {"name": "LegionForge", "entity_type": "organization", "confidence": 0.9},
        ]
        relations = self.ex.extract_relations(entities)
        triples = {(s, p, o) for s, p, o, _ in relations}
        assert ("JP Cruz", "member_of", "LegionForge") in triples

    def test_extract_relations_project_uses_tech(self):
        """project + technology → uses edge."""
        entities = [
            {"name": "Jeli", "entity_type": "project", "confidence": 0.9},
            {"name": "PostgreSQL", "entity_type": "technology", "confidence": 0.9},
        ]
        relations = self.ex.extract_relations(entities)
        triples = {(s, p, o) for s, p, o, _ in relations}
        assert ("Jeli", "uses", "PostgreSQL") in triples

    def test_extract_relations_org_develops_project(self):
        """organization + project → develops edge."""
        entities = [
            {"name": "LegionForge", "entity_type": "organization", "confidence": 0.9},
            {"name": "Jeli", "entity_type": "project", "confidence": 0.9},
        ]
        relations = self.ex.extract_relations(entities)
        triples = {(s, p, o) for s, p, o, _ in relations}
        assert ("LegionForge", "develops", "Jeli") in triples


# ── GraphStore ────────────────────────────────────────────────────────────────


def _mock_db(entity_row=None, relations=None, memory_rows=None, count=0):
    db = MagicMock()
    db.fetchval = AsyncMock(return_value=entity_row["id"] if entity_row else str(uuid.uuid4()))
    db.execute = AsyncMock()
    db.fetchrow = AsyncMock(return_value=entity_row)
    db.fetchall = AsyncMock(return_value=relations or memory_rows or [])
    return db


class TestGraphStore:
    @pytest.mark.asyncio
    async def test_upsert_entity_returns_id(self):
        entity_id = uuid.uuid4()
        db = _mock_db(entity_row={"id": entity_id})
        result = await GraphStore().upsert_entity(db, "JP Cruz", "person")
        assert result == str(entity_id)
        db.fetchval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_entity_is_called_with_name_and_type(self):
        db = _mock_db()
        await GraphStore().upsert_entity(db, "PostgreSQL", "technology")
        call_args = db.fetchval.call_args
        assert "PostgreSQL" in call_args.args
        assert "technology" in call_args.args

    @pytest.mark.asyncio
    async def test_link_memory_stores_relation(self):
        db = _mock_db()
        mid = str(uuid.uuid4())
        eid = str(uuid.uuid4())
        await GraphStore().link_memory(db, mid, eid, relation="mentions", confidence=0.9)
        db.execute.assert_awaited_once()
        sql = db.execute.call_args.args[0]
        assert "memory_entity_link" in sql

    @pytest.mark.asyncio
    async def test_link_memory_defaults_to_mentions(self):
        db = _mock_db()
        await GraphStore().link_memory(db, str(uuid.uuid4()), str(uuid.uuid4()))
        call_args = db.execute.call_args.args
        assert "mentions" in call_args

    @pytest.mark.asyncio
    async def test_record_relation_increments_evidence(self):
        db = _mock_db()
        sid = str(uuid.uuid4())
        oid = str(uuid.uuid4())
        await GraphStore().record_relation(db, sid, "works_on", oid)
        sql = db.execute.call_args.args[0]
        assert "evidence_count" in sql
        assert "entity_relation" in sql

    @pytest.mark.asyncio
    async def test_search_by_entity_returns_memories(self):
        mem_row = {
            "id": uuid.uuid4(),
            "content": "JP Cruz updated the memory system.",
            "trust_score": 0.8,
            "memory_type": "episodic",
            "created_at": datetime.now(UTC),
            "created_by": "jp-cruz",
            "source_agent": None,
            "content_class": "general",
            "metadata": None,
        }
        db = MagicMock()
        db.fetchall = AsyncMock(return_value=[mem_row])
        results = await GraphStore().search_by_entity(db, "JP Cruz", limit=10)
        assert len(results) == 1
        assert results[0]["content"] == "JP Cruz updated the memory system."
        assert "id" in results[0]
        assert "trust_score" in results[0]
        assert "effective_trust" in results[0]
        assert results[0]["content_class"] == "general"

    @pytest.mark.asyncio
    async def test_search_by_entity_clamps_limit(self):
        db = MagicMock()
        db.fetchall = AsyncMock(return_value=[])
        await GraphStore().search_by_entity(db, "Anyone", limit=9999)
        call_args = db.fetchall.call_args.args
        # The clamped limit (50) should be in the call args
        assert 50 in call_args

    @pytest.mark.asyncio
    async def test_get_entity_graph_structure_when_found(self):
        entity_row = {
            "id": uuid.uuid4(),
            "name": "Jeli",
            "entity_type": "project",
            "aliases": [],
            "metadata": {},
            "created_at": datetime.now(UTC),
        }
        relation_row = {
            "predicate": "works_on",
            "evidence_count": 3,
            "confidence": 0.9,
            "subject_name": "JP Cruz",
            "object_name": "Jeli",
            "outgoing": False,
        }
        db = MagicMock()
        db.fetchrow = AsyncMock(return_value=entity_row)
        db.fetchall = AsyncMock(return_value=[relation_row])
        db.fetchval = AsyncMock(return_value=5)

        result = await GraphStore().get_entity_graph(db, "Jeli")
        assert result["entity"]["name"] == "Jeli"
        assert result["entity"]["entity_type"] == "project"
        assert result["memory_count"] == 5
        assert len(result["relations"]) == 1
        assert result["relations"][0]["predicate"] == "works_on"

    @pytest.mark.asyncio
    async def test_get_entity_graph_when_not_found(self):
        db = MagicMock()
        db.fetchrow = AsyncMock(return_value=None)
        result = await GraphStore().get_entity_graph(db, "Nonexistent Entity XYZ")
        assert result["entity"] is None
        assert result["relations"] == []
        assert result["memory_count"] == 0


# ── Graph integration in capture pipeline ─────────────────────────────────────


class TestGraphCaptureIntegration:
    """Verify EntityExtractor + GraphStore are called during capture_memory
    when a graph_store is configured on MemoryTools."""

    @pytest.mark.asyncio
    async def test_capture_triggers_entity_extraction(self):
        """When graph_store is set, successful capture calls upsert_entity."""
        from jeli_scoped_mcp.tools.memory_tools import MemoryTools

        mock_graph = MagicMock()
        mock_graph.upsert_entity = AsyncMock(return_value=str(uuid.uuid4()))
        mock_graph.link_memory = AsyncMock()

        # Build a minimal MemoryTools with a fake DB and embedder.
        db = MagicMock()
        fake_id = uuid.uuid4()

        @asynccontextmanager
        async def fake_locked_transaction(lock):
            conn = MagicMock()
            conn.fetchval = AsyncMock(return_value="prevhash")
            conn.fetchrow = AsyncMock(return_value={
                "id": fake_id,
                "created_at": datetime.now(UTC),
            })
            conn.execute = AsyncMock()
            yield conn

        db.locked_transaction = fake_locked_transaction
        db.execute = AsyncMock()
        db.fetchall = AsyncMock(return_value=[])

        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=MagicMock(
            vector=[0.1] * 1024, model_id="test/embed", dimensions=1024,
            embedded_at=datetime.now(UTC),
        ))

        tools = MemoryTools(
            db=db, embedder=embedder, chain_key="testkey",
            key_id="k1", graph_store=mock_graph,
        )

        with patch("jeli_scoped_mcp.constitutional.manager.ConstitutionalManager") as mock_cm:
            mock_cm.return_value.load_active_rules = AsyncMock(return_value=[])
            await tools.capture_memory(
                content="JP Cruz added entity graph support to Jeli.",
                memory_type="episodic",
                trust_score=0.8,
                actor="jp-cruz",
            )

        # Entity extraction ran — graph store was called at least once
        assert mock_graph.upsert_entity.await_count >= 1
        # "JP Cruz" and/or "Jeli" should have been extracted and linked
        extracted_names = {
            call.args[1] for call in mock_graph.upsert_entity.call_args_list
        }
        assert "JP Cruz" in extracted_names or "Jeli" in extracted_names

    @pytest.mark.asyncio
    async def test_capture_records_relations_between_entities(self):
        """Capturing content with a person + project records a works_on relation."""
        from jeli_scoped_mcp.tools.memory_tools import MemoryTools

        # Give each entity name a distinct id so subj_id != obj_id and both truthy.
        ids = {}

        async def fake_upsert(db, name, entity_type, aliases=None):
            return ids.setdefault(name, str(uuid.uuid4()))

        mock_graph = MagicMock()
        mock_graph.upsert_entity = AsyncMock(side_effect=fake_upsert)
        mock_graph.link_memory = AsyncMock()
        mock_graph.record_relation = AsyncMock()

        db = MagicMock()
        fake_id = uuid.uuid4()

        @asynccontextmanager
        async def fake_locked_transaction(lock):
            conn = MagicMock()
            conn.fetchval = AsyncMock(return_value="prevhash")
            conn.fetchrow = AsyncMock(return_value={
                "id": fake_id,
                "created_at": datetime.now(UTC),
            })
            conn.execute = AsyncMock()
            yield conn

        db.locked_transaction = fake_locked_transaction
        db.execute = AsyncMock()
        db.fetchall = AsyncMock(return_value=[])

        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=MagicMock(
            vector=[0.1] * 1024, model_id="test/embed", dimensions=1024,
            embedded_at=datetime.now(UTC),
        ))

        tools = MemoryTools(
            db=db, embedder=embedder, chain_key="testkey",
            key_id="k1", graph_store=mock_graph,
        )

        with patch("jeli_scoped_mcp.constitutional.manager.ConstitutionalManager") as mock_cm:
            mock_cm.return_value.load_active_rules = AsyncMock(return_value=[])
            await tools.capture_memory(
                content="JP Cruz works on Jeli.",
                memory_type="episodic",
                trust_score=0.8,
                actor="jp-cruz",
            )

        mock_graph.record_relation.assert_awaited()
        call = mock_graph.record_relation.call_args
        # record_relation(db, subject_id, predicate, object_id)
        assert call.args[2] == "works_on"
        assert call.args[1] == ids["JP Cruz"]
        assert call.args[3] == ids["Jeli"]

    @pytest.mark.asyncio
    async def test_graph_failure_does_not_fail_capture(self):
        """A graph extraction error must not propagate — capture still succeeds."""
        from jeli_scoped_mcp.tools.memory_tools import MemoryTools

        mock_graph = MagicMock()
        mock_graph.upsert_entity = AsyncMock(side_effect=RuntimeError("db down"))

        db = MagicMock()
        fake_id = uuid.uuid4()

        @asynccontextmanager
        async def fake_locked_transaction(lock):
            conn = MagicMock()
            conn.fetchval = AsyncMock(return_value=None)
            conn.fetchrow = AsyncMock(return_value={
                "id": fake_id,
                "created_at": datetime.now(UTC),
            })
            conn.execute = AsyncMock()
            yield conn

        db.locked_transaction = fake_locked_transaction
        db.execute = AsyncMock()
        db.fetchall = AsyncMock(return_value=[])

        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=MagicMock(
            vector=[0.1] * 1024, model_id="test/embed", dimensions=1024,
            embedded_at=datetime.now(UTC),
        ))

        tools = MemoryTools(
            db=db, embedder=embedder, chain_key="testkey",
            key_id="k1", graph_store=mock_graph,
        )

        with patch("jeli_scoped_mcp.constitutional.manager.ConstitutionalManager") as mock_cm:
            mock_cm.return_value.load_active_rules = AsyncMock(return_value=[])
            result = await tools.capture_memory(
                content="GraphStore is broken but capture must still work.",
                memory_type="semantic",
                trust_score=0.6,
                actor="test-actor",
            )

        # Capture returned successfully even though graph exploded
        assert "id" in result
        assert str(result["id"]) == str(fake_id)

    @pytest.mark.asyncio
    async def test_no_graph_store_skips_extraction(self):
        """Without graph_store, capture must not call EntityExtractor."""
        from jeli_scoped_mcp.tools.memory_tools import MemoryTools

        db = MagicMock()
        fake_id = uuid.uuid4()

        @asynccontextmanager
        async def fake_locked_transaction(lock):
            conn = MagicMock()
            conn.fetchval = AsyncMock(return_value=None)
            conn.fetchrow = AsyncMock(return_value={
                "id": fake_id,
                "created_at": datetime.now(UTC),
            })
            conn.execute = AsyncMock()
            yield conn

        db.locked_transaction = fake_locked_transaction
        db.execute = AsyncMock()
        db.fetchall = AsyncMock(return_value=[])

        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=MagicMock(
            vector=[0.1] * 1024, model_id="test/embed", dimensions=1024,
            embedded_at=datetime.now(UTC),
        ))

        tools = MemoryTools(
            db=db, embedder=embedder, chain_key="testkey", key_id="k1"
            # graph_store deliberately omitted
        )

        with (
            patch("jeli_scoped_mcp.constitutional.manager.ConstitutionalManager") as mock_cm,
            patch("jeli_scoped_mcp.graph.extractor.EntityExtractor.extract") as mock_extract,
        ):
            mock_cm.return_value.load_active_rules = AsyncMock(return_value=[])
            await tools.capture_memory(
                content="JP Cruz is here.",
                memory_type="semantic",
                trust_score=0.7,
                actor="test-actor",
            )

        mock_extract.assert_not_called()
