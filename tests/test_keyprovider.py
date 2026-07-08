"""Unit tests for pluggable chain-key providers.

External-tool providers (keychain, 1password) are exercised through mocks; env,
file, and passphrase run for real (no services needed).
"""

import os
from unittest.mock import patch

import pytest

from jeli_scoped_mcp.config import Settings
from jeli_scoped_mcp.keyprovider import (
    KeyProviderError,
    available_providers,
    derive_key_from_passphrase,
    get_provider,
    resolve_chain_key,
)


def _settings(**kw) -> Settings:
    base = {"chain_key": "", "key_provider": "env", "key_ref": ""}
    base.update(kw)
    return Settings(**base)


# ── registry ─────────────────────────────────────────────────────────────────


def test_registry_lists_known_providers():
    assert set(available_providers()) == {
        "env",
        "file",
        "keychain",
        "1password",
        "passphrase",
    }


def test_unknown_provider_raises():
    with pytest.raises(KeyProviderError, match="unknown key provider"):
        get_provider("nope")


# ── env (default, backward compatible) ───────────────────────────────────────


def test_env_provider_returns_existing_key():
    s = _settings(chain_key="abc123", key_provider="env")
    assert resolve_chain_key(s) == "abc123"


def test_env_default_is_a_noop_passthrough():
    # The default provider must not alter historical behaviour.
    s = _settings(chain_key="unchanged")
    assert resolve_chain_key(s) == "unchanged"


def test_empty_resolved_key_raises():
    s = _settings(chain_key="", key_provider="env")
    with pytest.raises(KeyProviderError, match="empty chain key"):
        resolve_chain_key(s)


# ── file ─────────────────────────────────────────────────────────────────────


def test_file_provider_reads_and_strips(tmp_path):
    keyfile = tmp_path / "chain_key"
    keyfile.write_text("  file-sourced-key\n")
    os.chmod(keyfile, 0o600)
    s = _settings(key_provider="file", key_ref=str(keyfile))
    assert resolve_chain_key(s) == "file-sourced-key"


def test_file_provider_warns_on_loose_perms(tmp_path, caplog):
    keyfile = tmp_path / "chain_key"
    keyfile.write_text("k")
    os.chmod(keyfile, 0o644)
    s = _settings(key_provider="file", key_ref=str(keyfile))
    with caplog.at_level("WARNING"):
        resolve_chain_key(s)
    assert any("group/world accessible" in r.message for r in caplog.records)


def test_file_provider_missing_path_raises():
    s = _settings(key_provider="file", key_ref="")
    with pytest.raises(KeyProviderError, match="needs SCOPED_MCP_KEY_REF"):
        resolve_chain_key(s)


def test_file_provider_unreadable_raises(tmp_path):
    s = _settings(key_provider="file", key_ref=str(tmp_path / "nope"))
    with pytest.raises(KeyProviderError, match="not readable"):
        resolve_chain_key(s)


# ── passphrase (scrypt KDF) ──────────────────────────────────────────────────


def test_passphrase_derivation_is_deterministic():
    salt = bytes.fromhex("00112233445566778899aabbccddeeff")
    a = derive_key_from_passphrase("correct horse battery staple", salt)
    b = derive_key_from_passphrase("correct horse battery staple", salt)
    assert a == b and len(a) == 64  # 32 bytes hex


def test_passphrase_different_salt_different_key():
    a = derive_key_from_passphrase("pw", bytes.fromhex("00" * 16))
    b = derive_key_from_passphrase("pw", bytes.fromhex("11" * 16))
    assert a != b


def test_passphrase_provider_uses_env_when_not_prompting(monkeypatch):
    monkeypatch.setenv("SCOPED_MCP_PASSPHRASE", "unattended-pw")
    salt_hex = "00112233445566778899aabbccddeeff"
    s = _settings(key_provider="passphrase", key_ref=salt_hex)
    resolved = resolve_chain_key(s, prompt=False)
    expected = derive_key_from_passphrase("unattended-pw", bytes.fromhex(salt_hex))
    assert resolved == expected


def test_passphrase_provider_bad_salt_raises():
    s = _settings(key_provider="passphrase", key_ref="not-hex!!")
    with pytest.raises(KeyProviderError, match="not valid hex"):
        resolve_chain_key(s, prompt=False)


def test_passphrase_provider_no_passphrase_raises(monkeypatch):
    monkeypatch.delenv("SCOPED_MCP_PASSPHRASE", raising=False)
    s = _settings(key_provider="passphrase", key_ref="00" * 16)
    with pytest.raises(KeyProviderError, match="no passphrase"):
        resolve_chain_key(s, prompt=False)


# ── keychain (mocked) ────────────────────────────────────────────────────────


def test_keychain_provider_uses_keyring(monkeypatch):
    import types

    fake_keyring = types.SimpleNamespace(get_password=lambda svc, acct: "kc-key")
    monkeypatch.setitem(__import__("sys").modules, "keyring", fake_keyring)
    s = _settings(key_provider="keychain", key_ref="jeli-chain-key")
    assert resolve_chain_key(s) == "kc-key"


def test_keychain_provider_missing_entry_raises(monkeypatch):
    import types

    fake_keyring = types.SimpleNamespace(get_password=lambda svc, acct: None)
    monkeypatch.setitem(__import__("sys").modules, "keyring", fake_keyring)
    s = _settings(key_provider="keychain")
    with pytest.raises(KeyProviderError, match="no keychain entry"):
        resolve_chain_key(s)


# ── 1password (mocked) ───────────────────────────────────────────────────────


def test_onepassword_provider_reads_ref():
    from subprocess import CompletedProcess

    s = _settings(key_provider="1password", key_ref="op://Private/jeli/password")
    with patch(
        "jeli_scoped_mcp.keyprovider.providers.subprocess.run",
        return_value=CompletedProcess(args=[], returncode=0, stdout="op-key\n", stderr=""),
    ):
        assert resolve_chain_key(s) == "op-key"


def test_onepassword_provider_rejects_bad_ref():
    s = _settings(key_provider="1password", key_ref="/not/an/op/ref")
    with pytest.raises(KeyProviderError, match="op://"):
        resolve_chain_key(s)
