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
