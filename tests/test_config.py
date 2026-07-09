"""Unit tests for Settings.validate_required()."""

import pytest

from jeli_scoped_mcp.config import Settings


def _settings(**kw) -> Settings:
    base = {"chain_key": "", "key_provider": "env", "key_ref": ""}
    base.update(kw)
    return Settings(**base)


def test_validate_required_raises_on_empty_chain_key_with_env_provider():
    s = _settings(key_provider="env", chain_key="")
    with pytest.raises(ValueError, match="SCOPED_MCP_CHAIN_KEY is required"):
        s.validate_required()


def test_validate_required_passes_with_env_provider_and_chain_key_set():
    s = _settings(key_provider="env", chain_key="some-key")
    assert s.validate_required() is True


def test_validate_required_does_not_require_chain_key_for_other_providers():
    """Regression test: non-"env" providers resolve chain_key *after*
    get_settings() returns (see __main__.py), so it's legitimately empty at
    validate_required() time. Requiring it here made every non-"env" provider
    (openbao, keychain, 1password, passphrase) fail at real startup, even
    though unit tests for those providers passed (they call resolve() /
    Settings() directly, bypassing get_settings()'s eager validation).
    """
    for provider in ("openbao", "keychain", "1password", "passphrase", "file"):
        s = _settings(key_provider=provider, chain_key="", key_ref="placeholder")
        assert s.validate_required() is True


def test_validate_required_raises_on_http_transport_without_api_key():
    s = _settings(chain_key="k", transport="http", api_key="")
    with pytest.raises(ValueError, match="SCOPED_MCP_API_KEY is required"):
        s.validate_required()


def test_validate_required_raises_on_openai_provider_without_api_key():
    s = _settings(chain_key="k", embedding_provider="openai", openai_api_key="")
    with pytest.raises(ValueError, match="OPENAI_API_KEY is required"):
        s.validate_required()
