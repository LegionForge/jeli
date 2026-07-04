"""Tests for the jeli CLI (verify subcommand)."""

import json

import pytest

from jeli_scoped_mcp import cli


class FakeSettings:
    chain_key = "test-chain-key"
    db_url = "postgresql://unused"


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
