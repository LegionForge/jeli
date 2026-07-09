"""Pluggable chain-key providers.

Jeli's hash chain is keyed by a single HMAC secret (`chain_key`). Where that
secret comes from should not be hard-coded to an environment variable, so key
sourcing is pluggable.

Two tiers of provider exist conceptually:

- **Key-material providers (implemented here).** They *return* the key bytes,
  which then flow through the existing local HMAC path unchanged: env, a
  0600 file, the OS keychain, 1Password, or a passphrase-derived key. Selected
  by ``SCOPED_MCP_KEY_PROVIDER`` and resolved once at startup.

- **Signing-oracle providers (planned, not yet implemented).** They never
  release the key; you send a payload and get a MAC/signature back (OpenBAO
  transit, cloud KMS, an HSM, a FIDO2 authenticator). Supporting these needs an
  async ``Signer`` contract (``mac(payload)`` / ``verify(payload, mac)``) and a
  refactor of the synchronous ``compute_record_hash`` hot path, so they are
  tracked separately. The ``Signer`` protocol below documents the intended
  shape.

Security: a provider returns secret material to the caller, which assigns it to
``settings.chain_key`` and never logs it. Providers must never print or echo the
key.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..config import Settings


class KeyProviderError(Exception):
    """Raised when a key provider cannot resolve the chain key."""


class KeyProvider(ABC):
    """Resolves the chain-key material from some backing store.

    Implementations are selected by name via ``SCOPED_MCP_KEY_PROVIDER`` and
    resolved exactly once at process startup. ``resolve`` may prompt or shell
    out; it must never be called at import or during ``Settings()`` construction
    (tests construct Settings freely, and must not trigger a keychain unlock).
    """

    name: str

    @abstractmethod
    def resolve(self, settings: Settings, *, prompt: bool = True) -> str:
        """Return the chain-key material as a string.

        `prompt` allows interactive providers (passphrase) to fall back to a
        non-interactive source when running unattended.
        """


@runtime_checkable
class Signer(Protocol):
    """Planned contract for signing-oracle providers (Tier 2, not implemented).

    A signing oracle never releases key material; it computes the MAC for a
    payload and verifies one. Adopting this lets OpenBAO transit / KMS / HSM /
    FIDO2 back the chain without the secret ever living in Jeli's memory, at the
    cost of an async call on the write and verify paths.
    """

    async def mac(self, payload: bytes) -> str: ...

    async def verify(self, payload: bytes, mac: str) -> bool: ...


_REGISTRY: dict[str, type[KeyProvider]] = {}


def register(cls: type[KeyProvider]) -> type[KeyProvider]:
    """Class decorator: add a provider to the name registry."""
    _REGISTRY[cls.name] = cls
    return cls


def available_providers() -> list[str]:
    """Names of registered key-material providers."""
    return sorted(_REGISTRY)


def get_provider(name: str) -> KeyProvider:
    """Instantiate a provider by name, or raise with the valid set."""
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise KeyProviderError(
            f"unknown key provider {name!r}; available: {available_providers()}"
        ) from None


def resolve_chain_key(settings: Settings, *, prompt: bool = True) -> str:
    """Resolve the chain key via the configured provider.

    Called once at startup. Default provider ``env`` returns the value already
    on ``settings.chain_key``, so the historical behaviour is unchanged unless a
    provider is explicitly selected.
    """
    provider = get_provider(settings.key_provider)
    key = provider.resolve(settings, prompt=prompt)
    if not key:
        raise KeyProviderError(
            f"key provider {settings.key_provider!r} resolved an empty chain key"
        )
    return key
