"""Tests for the jeli CLI (verify subcommand)."""

import json
from types import SimpleNamespace

import pytest

from jeli_scoped_mcp import cli
from jeli_scoped_mcp.constitutional.manager import ConstitutionalError
from jeli_scoped_mcp.core.hash_chain import build_canonical_record, compute_record_hash


class FakeSettings:
    chain_key = "test-chain-key"
    chain_key_id = "k1"
    db_url = "postgresql://unused"
    key_provider = "env"  # default: CLI skips provider resolution
    key_ref = ""


def _patch(monkeypatch, verify_result, chain_key="test-chain-key"):
    async def fake_run_verify(settings):
        return verify_result

    monkeypatch.setattr(cli, "_run_verify", fake_run_verify)
    settings = FakeSettings()
    settings.chain_key = chain_key
    monkeypatch.setattr(cli, "Settings", lambda: settings)


def test_verify_valid_chain_exit_0(monkeypatch, capsys):
    _patch(
        monkeypatch,
        {
            "chain_valid": True,
            "records_checked": 3,
            "first_bad_record": None,
            "state_chain_valid": True,
            "events_checked": 1,
            "cache_consistent": True,
        },
    )
    assert cli.main(["verify"]) == 0
    assert "chains valid" in capsys.readouterr().out


def test_verify_broken_chain_exit_1(monkeypatch, capsys):
    _patch(
        monkeypatch,
        {"chain_valid": False, "records_checked": 3, "first_bad_record": "abc"},
    )
    assert cli.main(["verify"]) == 1
    assert "CHAIN BROKEN" in capsys.readouterr().out


def test_verify_json_output(monkeypatch, capsys):
    result = {"chain_valid": True, "records_checked": 0, "first_bad_record": None}
    _patch(monkeypatch, result)
    assert cli.main(["verify", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_verify_missing_chain_key_exit_2(monkeypatch, capsys):
    _patch(monkeypatch, {}, chain_key="")
    assert cli.main(["verify"]) == 2
    assert "SCOPED_MCP_CHAIN_KEY" in capsys.readouterr().err


def test_no_command_exits(monkeypatch):
    with pytest.raises(SystemExit):
        cli.main([])


async def test_constitutional_mutation_requires_operator_db_url(monkeypatch):
    settings = FakeSettings()
    settings.constitutional_db_url = ""

    def unexpected_pool(**_kwargs):
        raise AssertionError("must reject before opening the runtime database")

    monkeypatch.setattr(cli, "AsyncPostgresPool", unexpected_pool)
    args = SimpleNamespace(constitutional_cmd="revoke", rule_id="rule-id")

    with pytest.raises(ConstitutionalError, match="CONSTITUTIONAL_DB_URL"):
        await cli._run_constitutional(settings, args)


async def test_constitutional_mutation_uses_operator_db_url(monkeypatch):
    opened_urls = []

    class FakePool:
        def __init__(self, db_url, **_kwargs):
            opened_urls.append(db_url)

        async def connect(self):
            pass

        async def fetchrow(self, query, *_args):
            if query.strip().startswith("SELECT r.id, r.rule_hash"):
                return {
                    "id": "rule-id",
                    "rule_hash": "body-hash",
                    "event_at": datetime.now(UTC),
                }
            return {"id": "event-id"}

        async def close(self):
            pass

    settings = FakeSettings()
    settings.constitutional_db_url = "postgresql://constitutional-operator"
    monkeypatch.setattr(cli, "AsyncPostgresPool", FakePool)

    result = await cli._run_constitutional(
        settings, SimpleNamespace(constitutional_cmd="revoke", rule_id="rule-id")
    )

    assert result["revoked"] == "rule-id"
    assert opened_urls == [settings.constitutional_db_url]


def test_verify_cache_mismatch_exit_1(monkeypatch, capsys):
    _patch(
        monkeypatch,
        {
            "chain_valid": True,
            "records_checked": 3,
            "first_bad_record": None,
            "state_chain_valid": True,
            "events_checked": 1,
            "cache_consistent": False,
            "mismatches": ["abc: retired in columns, no chained event"],
        },
    )
    assert cli.main(["verify"]) == 1


# ── inbox approve / reject / retry ───────────────────────────────────────────


class FakeSettingsInbox(FakeSettings):
    chain_key_id = "k1"
    inbox_enabled = True
    embedding_provider = "ollama"
    ollama_base_url = "http://unused"
    ollama_model = "snowflake-arctic-embed2"
    embedding_dimensions = 1024
    embed_keep_alive = "30m"


def _patch_inbox(monkeypatch, inbox_cmd_fn, fn_name, chain_key="test-key"):
    monkeypatch.setattr(cli, fn_name, inbox_cmd_fn)
    settings = FakeSettingsInbox()
    settings.chain_key = chain_key
    monkeypatch.setattr(cli, "Settings", lambda: settings)


def test_inbox_approve_success(monkeypatch, capsys):
    async def fake_approve(settings, inbox_id, actor):
        return {"approved": inbox_id, "promoted_to": "mem-uuid-1"}

    _patch_inbox(monkeypatch, fake_approve, "_run_inbox_approve")
    assert cli.main(["inbox", "approve", "inbox-abc", "--actor", "jp"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["approved"] == "inbox-abc"
    assert out["promoted_to"] == "mem-uuid-1"


def test_inbox_approve_not_held(monkeypatch, capsys):
    async def fake_approve(settings, inbox_id, actor):
        raise ValueError(f"inbox item {inbox_id} not found or not in 'held' status")

    _patch_inbox(monkeypatch, fake_approve, "_run_inbox_approve")
    assert cli.main(["inbox", "approve", "bad-id"]) == 1
    assert "not found" in capsys.readouterr().err


def test_inbox_reject_success(monkeypatch, capsys):
    async def fake_reject(settings, inbox_id, reason):
        return {"rejected": inbox_id, "reason": reason}

    _patch_inbox(monkeypatch, fake_reject, "_run_inbox_reject")
    assert cli.main(["inbox", "reject", "inbox-xyz", "--reason", "duplicate content"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rejected"] == "inbox-xyz"
    assert out["reason"] == "duplicate content"


def test_inbox_reject_not_held(monkeypatch, capsys):
    async def fake_reject(settings, inbox_id, reason):
        raise ValueError("not found")

    _patch_inbox(monkeypatch, fake_reject, "_run_inbox_reject")
    assert cli.main(["inbox", "reject", "bad-id", "--reason", "spam"]) == 1
    assert "not found" in capsys.readouterr().err


def test_inbox_retry_success(monkeypatch, capsys):
    async def fake_retry(settings, inbox_id):
        return {"retrying": inbox_id}

    _patch_inbox(monkeypatch, fake_retry, "_run_inbox_retry")
    assert cli.main(["inbox", "retry", "inbox-abc"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["retrying"] == "inbox-abc"


def test_inbox_retry_not_held(monkeypatch, capsys):
    async def fake_retry(settings, inbox_id):
        raise ValueError("not found")

    _patch_inbox(monkeypatch, fake_retry, "_run_inbox_retry")
    assert cli.main(["inbox", "retry", "bad-id"]) == 1
    assert "not found" in capsys.readouterr().err


def test_inbox_status_prints_counts(monkeypatch, capsys):
    async def fake_status(settings):
        return {"held": 3, "pending": 1, "approved": 42}

    _patch_inbox(monkeypatch, fake_status, "_run_inbox_status")
    assert cli.main(["inbox", "status"]) == 0
    out = capsys.readouterr().out
    assert "held" in out
    assert "42" in out


def test_inbox_review_empty(monkeypatch, capsys):
    async def fake_review(settings, limit):
        return []

    _patch_inbox(monkeypatch, fake_review, "_run_inbox_review")
    assert cli.main(["inbox", "review"]) == 0
    assert "no held" in capsys.readouterr().out


def test_inbox_review_returns_json(monkeypatch, capsys):
    async def fake_review(settings, limit):
        return [{"id": "abc", "content": "test memory", "source_agent": "hermes",
                 "submitted_at": "2026-07-04T00:00:00", "review_reason": "near-dup",
                 "caller_type": "episodic", "caller_trust": 0.6,
                 "retry_count": 0, "error": None}]

    _patch_inbox(monkeypatch, fake_review, "_run_inbox_review")
    assert cli.main(["inbox", "review", "--limit", "5"]) == 0
    items = json.loads(capsys.readouterr().out)
    assert len(items) == 1
    assert items[0]["id"] == "abc"


# ── re-embed / decay-report ──────────────────────────────────────────────────

from datetime import UTC, datetime, timedelta  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402


def _mock_pool(monkeypatch, *, fetchall=None, fetchrow=None):
    db = MagicMock()
    db.connect = AsyncMock()
    db.close = AsyncMock()
    db.fetchall = AsyncMock(return_value=fetchall or [])
    db.fetchrow = AsyncMock(return_value=fetchrow)
    db.execute = AsyncMock(return_value="UPDATE 1")
    monkeypatch.setattr(cli, "AsyncPostgresPool", lambda **kw: db)
    return db


@pytest.mark.asyncio
async def test_decay_report_filters_by_threshold(monkeypatch):
    now = datetime.now(UTC)
    rows = [
        # decayed >20% (0.8 → clamped 0.3 over 200d): included
        {"id": "aaa", "content": "old low-ish", "trust_score": 0.8,
         "memory_type": "episodic", "created_at": now - timedelta(days=200)},
        # barely aged (0.85, 5d ≈ 0.81): NOT >20% decay, excluded
        {"id": "bbb", "content": "fresh", "trust_score": 0.85,
         "memory_type": "semantic", "created_at": now - timedelta(days=5)},
        # user-confirmed (>=0.9) never decays: excluded
        {"id": "ccc", "content": "confirmed", "trust_score": 0.95,
         "memory_type": "identity", "created_at": now - timedelta(days=300)},
    ]
    _mock_pool(monkeypatch, fetchall=rows)
    settings = FakeSettings()

    report = await cli._run_decay_report(settings, days=90, min_stored_trust=0.7)

    assert [r["id"] for r in report] == ["aaa"]
    assert report[0]["stored_trust"] == 0.8
    assert report[0]["effective_trust"] < 0.8 * 0.8


@pytest.mark.asyncio
async def test_re_embed_dry_run_no_writes(monkeypatch):
    import jeli_scoped_mcp.embedding.provider as prov

    fake_embedder = MagicMock()
    fake_embedder.model_id = MagicMock(return_value="ollama/current-model")
    fake_embedder.embed = AsyncMock()
    monkeypatch.setattr(prov.EmbeddingProvider, "from_settings", lambda s: fake_embedder)

    db = _mock_pool(
        monkeypatch,
        fetchall=[{"id": "s1"}, {"id": "s2"}],
        fetchrow={"c": 2},
    )
    settings = FakeSettings()

    result = await cli._run_re_embed(settings, dry_run=True, batch_size=50, model=None)

    assert result["dry_run"] is True
    assert result["total_stale"] == 2
    assert result["sample_ids"] == ["s1", "s2"]
    db.execute.assert_not_awaited()
    fake_embedder.embed.assert_not_awaited()


# ── graph / export / import ──────────────────────────────────────────────────


def _patch_run(monkeypatch, fn_name, mock):
    """Replace an async _run_* with a mock and stub Settings (no real DB/env)."""
    monkeypatch.setattr(cli, fn_name, mock)
    settings = FakeSettings()
    settings.chain_key = "test-key"
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    return mock


# graph --------------------------------------------------------------------------


def _graph_memory_row(*, content: str, record_hash: str | None = None) -> dict:
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "content": content,
        "trust_score": 0.8,
        "memory_type": "semantic",
        "metadata": {},
        "embedding_model": "test/model",
        "embedding_dimensions": 1024,
        "prev_hash": None,
        "key_id": "k1",
    }
    canonical = build_canonical_record(
        content=row["content"],
        embedding_model=row["embedding_model"],
        embedding_dimensions=row["embedding_dimensions"],
        trust_score=row["trust_score"],
        memory_type=row["memory_type"],
        key_id=row["key_id"],
        metadata=None,
    )
    row["record_hash"] = record_hash or compute_record_hash(
        FakeSettings.chain_key, canonical, None
    )
    return row

def test_graph_entities_prints_json(monkeypatch, capsys):
    run = _patch_run(
        monkeypatch,
        "_run_graph",
        AsyncMock(
            return_value=[
                {"name": "JP Cruz", "entity_type": "person",
                 "created_at": "2026-01-01T00:00:00"}
            ]
        ),
    )
    assert cli.main(["graph", "entities"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, list)
    assert out[0]["name"] == "JP Cruz"
    run.assert_awaited_once()


def test_graph_entities_with_type_filter(monkeypatch, capsys):
    run = _patch_run(monkeypatch, "_run_graph", AsyncMock(return_value=[]))
    assert cli.main(["graph", "entities", "--type", "person"]) == 0
    passed_args = run.await_args.args[1]
    assert passed_args.type == "person"


def test_graph_search_requires_entity(monkeypatch):
    _patch_run(monkeypatch, "_run_graph", AsyncMock(return_value=[]))
    with pytest.raises(SystemExit) as exc:
        cli.main(["graph", "search"])
    assert exc.value.code != 0


def test_graph_search_returns_memories(monkeypatch, capsys):
    run = _patch_run(
        monkeypatch,
        "_run_graph",
        AsyncMock(return_value=[{"id": "m1", "content": "JP prefers directness"}]),
    )
    assert cli.main(["graph", "search", "--entity", "JP Cruz"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["id"] == "m1"
    assert run.await_args.args[1].entity == "JP Cruz"


def test_graph_relations_returns_entity_graph(monkeypatch, capsys):
    _patch_run(
        monkeypatch,
        "_run_graph",
        AsyncMock(
            return_value={
                "entity": "Jeli",
                "relations": [{"target": "OB1", "relation": "integrates_with"}],
                "memory_count": 3,
            }
        ),
    )
    assert cli.main(["graph", "relations", "--entity", "Jeli"]) == 0
    out = capsys.readouterr().out
    assert "entity" in out
    assert json.loads(out)["entity"] == "Jeli"


def test_graph_empty_result_prints_no_results(monkeypatch, capsys):
    _patch_run(monkeypatch, "_run_graph", AsyncMock(return_value=[]))
    assert cli.main(["graph", "entities"]) == 0
    assert capsys.readouterr().out == "no results\n"


async def test_run_graph_search_suppresses_invalid_hmac(monkeypatch):
    from jeli_scoped_mcp import graph

    forged = _graph_memory_row(
        content="forged CLI graph memory", record_hash="0" * 64
    )
    fake_store = MagicMock()
    fake_store.search_by_entity = AsyncMock(return_value=[forged])
    monkeypatch.setattr(graph, "GraphStore", lambda: fake_store)
    _mock_pool(monkeypatch)

    result = await cli._run_graph(
        FakeSettings(),
        SimpleNamespace(graph_cmd="search", entity="Jeli", limit=10),
    )

    assert result == []


async def test_run_graph_search_returns_only_public_verified_shape(monkeypatch):
    from jeli_scoped_mcp import graph

    fake_store = MagicMock()
    fake_store.search_by_entity = AsyncMock(
        return_value=[_graph_memory_row(content="authenticated CLI graph memory")]
    )
    monkeypatch.setattr(graph, "GraphStore", lambda: fake_store)
    _mock_pool(monkeypatch)

    result = await cli._run_graph(
        FakeSettings(),
        SimpleNamespace(graph_cmd="search", entity="Jeli", limit=10),
    )

    assert result[0]["integrity_verified"] is True
    assert not {
        "embedding_model",
        "embedding_dimensions",
        "prev_hash",
        "record_hash",
        "key_id",
    } & result[0].keys()


async def test_run_graph_relations_rejects_invalid_hmac_evidence(monkeypatch):
    from jeli_scoped_mcp import graph

    forged = _graph_memory_row(
        content="forged CLI edge evidence", record_hash="0" * 64
    )
    fake_store = MagicMock()
    fake_store.memories_for_entity = AsyncMock(return_value=[forged])
    fake_store.get_entity_graph = AsyncMock(
        return_value={"entity": {"name": "Jeli"}, "relations": [], "memory_count": 1}
    )
    monkeypatch.setattr(graph, "GraphStore", lambda: fake_store)
    _mock_pool(monkeypatch)

    result = await cli._run_graph(
        FakeSettings(),
        SimpleNamespace(graph_cmd="relations", entity="Jeli"),
    )

    assert result == {"entity": None, "relations": [], "memory_count": 0}
    fake_store.get_entity_graph.assert_not_awaited()


async def test_run_graph_relations_uses_only_authenticated_evidence(monkeypatch):
    from jeli_scoped_mcp import graph

    evidence = _graph_memory_row(content="authenticated CLI edge evidence")
    fake_store = MagicMock()
    fake_store.memories_for_entity = AsyncMock(return_value=[evidence])
    fake_store.get_entity_graph = AsyncMock(
        return_value={"entity": {"name": "Jeli"}, "relations": [], "memory_count": 1}
    )
    monkeypatch.setattr(graph, "GraphStore", lambda: fake_store)
    db = _mock_pool(monkeypatch)

    await cli._run_graph(
        FakeSettings(),
        SimpleNamespace(graph_cmd="relations", entity="Jeli"),
    )

    fake_store.get_entity_graph.assert_awaited_once_with(
        db,
        "Jeli",
        visible_memory_ids={evidence["id"]},
    )


# export -------------------------------------------------------------------------

def test_export_prints_summary_to_stderr(monkeypatch, capsys):
    _patch_run(
        monkeypatch,
        "_run_export",
        AsyncMock(return_value={"record_count": 5, "chain_valid": True}),
    )
    assert cli.main(["export"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["record_count"] == 5


def test_export_with_output_flag(monkeypatch, capsys, tmp_path):
    run = _patch_run(
        monkeypatch,
        "_run_export",
        AsyncMock(return_value={"record_count": 0, "chain_valid": True}),
    )
    out_file = str(tmp_path / "archive.jsonl")
    assert cli.main(["export", "--output", out_file]) == 0
    assert run.await_args.args[1].output == out_file


# import -------------------------------------------------------------------------

def test_import_prints_summary(monkeypatch, capsys, tmp_path):
    archive = tmp_path / "in.jsonl"
    archive.touch()
    _patch_run(
        monkeypatch,
        "_run_import",
        AsyncMock(return_value={"imported": 3, "skipped_tampered": 0}),
    )
    assert cli.main(["import", str(archive)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["imported"] == 3


def test_import_dry_run_flag(monkeypatch, capsys, tmp_path):
    archive = tmp_path / "in.jsonl"
    archive.touch()
    run = _patch_run(
        monkeypatch,
        "_run_import",
        AsyncMock(return_value={"imported": 0, "skipped_tampered": 0, "dry_run": True}),
    )
    assert cli.main(["import", str(archive), "--dry-run"]) == 0
    assert run.await_args.args[1].dry_run is True


def test_import_bad_archive_prints_error(monkeypatch, capsys, tmp_path):
    from jeli_scoped_mcp.portability.importer import ImportError as ArchiveImportError

    archive = tmp_path / "in.jsonl"
    archive.touch()
    _patch_run(
        monkeypatch,
        "_run_import",
        AsyncMock(side_effect=ArchiveImportError("empty import archive")),
    )
    assert cli.main(["import", str(archive)]) == 1
    assert "error:" in capsys.readouterr().err


# ── health ────────────────────────────────────────────────────────────────────


def _patch_health(monkeypatch, health_result, chain_key="test-chain-key"):
    async def fake_run_health(settings):
        return health_result

    monkeypatch.setattr(cli, "_run_health", fake_run_health)
    settings = FakeSettings()
    settings.chain_key = chain_key
    monkeypatch.setattr(cli, "Settings", lambda: settings)


def test_health_all_ok_exit_0(monkeypatch, capsys):
    _patch_health(
        monkeypatch,
        {"ok": True, "checks": {"postgres": {"ok": True, "active_memories": 5}}},
    )
    assert cli.main(["health"]) == 0
    out = capsys.readouterr().out
    assert "postgres" in out
    assert "ok" in out


def test_health_failing_check_exit_1(monkeypatch, capsys):
    _patch_health(
        monkeypatch,
        {
            "ok": False,
            "checks": {"ollama": {"ok": False, "error": "connection refused"}},
        },
    )
    assert cli.main(["health"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "connection refused" in out


def test_health_json_output(monkeypatch, capsys):
    result = {"ok": True, "checks": {"postgres": {"ok": True}}}
    _patch_health(monkeypatch, result)
    assert cli.main(["health", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_health_runs_without_chain_key(monkeypatch, capsys):
    """health must not require a resolvable chain key — a broken provider is a
    finding it reports, not a startup precondition (unlike every other cmd)."""
    _patch_health(
        monkeypatch,
        {"ok": False, "checks": {"chain_key": {"ok": False, "error": "no key"}}},
        chain_key="",
    )
    assert cli.main(["health"]) == 1  # not the exit-2 config error path


@pytest.mark.asyncio
async def test_run_health_env_provider_missing_key(monkeypatch):
    """_run_health with env provider and empty key: chain_key check fails,
    postgres failure is captured as an error rather than raised."""
    settings = FakeSettings()
    settings.chain_key = ""
    settings.embedding_provider = "openai"  # skip the ollama check
    settings.litellm_base_url = ""

    class BoomPool:
        def __init__(self, **kw):
            pass

        async def connect(self):
            raise OSError("db down")

    monkeypatch.setattr(cli, "AsyncPostgresPool", BoomPool)
    result = await cli._run_health(settings)
    assert result["ok"] is False
    assert result["checks"]["postgres"]["ok"] is False
    assert "db down" in result["checks"]["postgres"]["error"]
    assert result["checks"]["chain_key"]["ok"] is False
    assert "openbao" not in result["checks"]
    assert "ollama" not in result["checks"]
    assert "litellm" not in result["checks"]
