"""Pluggable chain-key providers. See base.py for the two-tier design."""

from .base import (
    KeyProvider,
    KeyProviderError,
    Signer,
    available_providers,
    get_provider,
    resolve_chain_key,
)
from .providers import (
    EnvKeyProvider,
    FileKeyProvider,
    KeychainKeyProvider,
    OnePasswordKeyProvider,
    OpenBAOKeyProvider,
    PassphraseKeyProvider,
    derive_key_from_passphrase,
)

__all__ = [
    "KeyProvider",
    "KeyProviderError",
    "Signer",
    "available_providers",
    "get_provider",
    "resolve_chain_key",
    "EnvKeyProvider",
    "FileKeyProvider",
    "KeychainKeyProvider",
    "OnePasswordKeyProvider",
    "OpenBAOKeyProvider",
    "PassphraseKeyProvider",
    "derive_key_from_passphrase",
]
