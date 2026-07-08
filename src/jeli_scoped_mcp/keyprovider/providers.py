"""Concrete key-material providers (Tier 1: they return the key bytes).

Each reads its configuration from Settings so nothing secret is passed on a
command line. None of them log or echo the key.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import subprocess  # nosec B404 — used with fixed argv, no shell
from typing import TYPE_CHECKING

from .base import KeyProvider, KeyProviderError, register

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger(__name__)


@register
class EnvKeyProvider(KeyProvider):
    """Default: the key already loaded from the environment / .env file.

    Preserves Jeli's historical behaviour exactly.
    """

    name = "env"

    def resolve(self, settings: Settings, *, prompt: bool = True) -> str:
        return settings.chain_key


@register
class FileKeyProvider(KeyProvider):
    """Read the key from a dedicated file (e.g. ``~/.jeli-secrets/chain_key``).

    Warns loudly if the file is group- or world-readable, since a chain key is a
    root-grade credential.
    """

    name = "file"

    def resolve(self, settings: Settings, *, prompt: bool = True) -> str:
        path = os.path.expanduser(settings.key_ref)
        if not path:
            raise KeyProviderError("key provider 'file' needs SCOPED_MCP_KEY_REF (a path)")
        try:
            st = os.stat(path)
        except OSError as exc:
            raise KeyProviderError(f"key file not readable: {exc}") from exc
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            logger.warning(
                "chain key file %s is group/world accessible; chmod 600 it", path
            )
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()


@register
class KeychainKeyProvider(KeyProvider):
    """Read the key from the OS keychain.

    Prefers the ``keyring`` library (cross-platform, optional dependency); falls
    back to the macOS ``security`` CLI. ``SCOPED_MCP_KEY_REF`` is the service
    name (default ``jeli-chain-key``); the account is the chain key id.
    """

    name = "keychain"

    def resolve(self, settings: Settings, *, prompt: bool = True) -> str:
        service = settings.key_ref or "jeli-chain-key"
        account = settings.chain_key_id
        try:
            import keyring

            value = keyring.get_password(service, account)
            if value:
                return str(value).strip()
            raise KeyProviderError(
                f"no keychain entry for service={service!r} account={account!r}"
            )
        except ImportError:
            pass  # fall back to the macOS CLI
        try:
            out = subprocess.run(  # nosec B603 B607 — fixed argv, no shell
                ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            return out.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise KeyProviderError(
                "keychain lookup failed (install the 'keyring' extra or use macOS "
                f"security): {exc}"
            ) from exc


@register
class OnePasswordKeyProvider(KeyProvider):
    """Read the key from 1Password via the ``op`` CLI.

    ``SCOPED_MCP_KEY_REF`` is a secret reference such as
    ``op://Private/jeli-chain-key/password``. Requires the ``op`` CLI installed
    and signed in.
    """

    name = "1password"

    def resolve(self, settings: Settings, *, prompt: bool = True) -> str:
        ref = settings.key_ref
        if not ref or not ref.startswith("op://"):
            raise KeyProviderError(
                "key provider '1password' needs SCOPED_MCP_KEY_REF = op://vault/item/field"
            )
        try:
            out = subprocess.run(  # nosec B603 B607 — fixed argv, no shell
                ["op", "read", ref],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            return out.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise KeyProviderError(
                f"1Password read failed (is 'op' installed and signed in?): {exc}"
            ) from exc


@register
class PassphraseKeyProvider(KeyProvider):
    """Derive the chain key from a user passphrase via scrypt (PGP-style).

    The passphrase is entered interactively (never stored); a fixed salt makes
    the derived key reproducible across restarts. ``SCOPED_MCP_KEY_REF`` holds
    the salt as hex. When ``prompt`` is False (unattended), reads the passphrase
    from ``SCOPED_MCP_PASSPHRASE`` instead.

    Derivation: scrypt(passphrase, salt, n=2**15, r=8, p=1, dklen=32) -> hex.
    """

    name = "passphrase"

    def resolve(self, settings: Settings, *, prompt: bool = True) -> str:
        salt_hex = settings.key_ref
        if not salt_hex:
            raise KeyProviderError(
                "key provider 'passphrase' needs SCOPED_MCP_KEY_REF = salt (hex); "
                "generate one with: python -c 'import secrets;print(secrets.token_hex(16))'"
            )
        try:
            salt = bytes.fromhex(salt_hex)
        except ValueError as exc:
            raise KeyProviderError(f"passphrase salt is not valid hex: {exc}") from exc

        passphrase = os.environ.get("SCOPED_MCP_PASSPHRASE")
        if not passphrase and prompt:
            import getpass

            passphrase = getpass.getpass("Jeli chain-key passphrase: ")
        if not passphrase:
            raise KeyProviderError(
                "no passphrase provided (set SCOPED_MCP_PASSPHRASE or run interactively)"
            )
        return derive_key_from_passphrase(passphrase, salt)


def derive_key_from_passphrase(passphrase: str, salt: bytes) -> str:
    """scrypt KDF -> hex chain key. Pure and deterministic given passphrase+salt.

    n=2**15,r=8,p=1 needs ~32 MB, which is exactly OpenSSL's default maxmem
    ceiling, so maxmem is raised explicitly rather than weakening the KDF.
    """
    return hashlib.scrypt(
        passphrase.encode(),
        salt=salt,
        n=2**15,
        r=8,
        p=1,
        dklen=32,
        maxmem=128 * 1024 * 1024,
    ).hex()
