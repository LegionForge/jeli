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


def _reject_flag_like(value: str, label: str) -> None:
    """Guard against config values a target CLI could misparse as an option.

    subprocess.run with a fixed argv list has no shell injection surface, but
    a config-controlled value starting with '-' can still be read as a flag
    by the target CLI's own argument parser instead of the positional data it
    was meant to be. Reject that shape rather than pass it through.
    """
    if value.startswith("-"):
        raise KeyProviderError(f"{label} must not start with '-': {value!r}")


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
        _reject_flag_like(service, "SCOPED_MCP_KEY_REF")
        _reject_flag_like(account, "chain key id")
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
            out = subprocess.run(  # nosec B603 B607 — shell=False, fixed argv; service/account validated above
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
            out = subprocess.run(  # nosec B603 B607 — shell=False, fixed argv; ref is checked to start with "op://" above, so it can't be misread as a flag
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
class OpenBAOKeyProvider(KeyProvider):
    """Read the key from an OpenBAO (or Vault) KV secret via the ``bao`` CLI.

    This is the key-*material* use of OpenBAO: the vault stores and access-
    controls the chain key, and Jeli reads it at startup. It does not keep the
    key inside the vault (that is the transit signing-oracle tier, not yet
    built), but it centralises custody, audit, and rotation.

    ``SCOPED_MCP_KEY_REF`` is ``<kv-path>#<field>`` (field defaults to
    ``value``), e.g. ``secret/jeli-chain-key#value``. The CLI uses the
    ambient ``BAO_ADDR`` / ``BAO_TOKEN`` environment (OpenBAO must be unsealed
    and the caller authenticated). ``bao`` is a drop-in for ``vault``; set
    ``SCOPED_MCP_KEY_REF`` and the CLI name is fixed to ``bao``.
    """

    name = "openbao"

    def resolve(self, settings: Settings, *, prompt: bool = True) -> str:
        ref = settings.key_ref
        if not ref:
            raise KeyProviderError(
                "key provider 'openbao' needs SCOPED_MCP_KEY_REF = <kv-path>#<field> "
                "(e.g. secret/jeli-chain-key#value)"
            )
        path, _, field = ref.partition("#")
        field = field or "value"
        _reject_flag_like(path, "SCOPED_MCP_KEY_REF path")
        _reject_flag_like(field, "SCOPED_MCP_KEY_REF field")
        try:
            out = subprocess.run(  # nosec B603 B607 — shell=False, fixed argv; path/field validated above
                ["bao", "kv", "get", "-field", field, path],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            key = out.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise KeyProviderError(
                "OpenBAO read failed (is 'bao' installed, BAO_ADDR/BAO_TOKEN set, "
                f"and the vault unsealed?): {exc}"
            ) from exc
        self._warn_if_writable(path)
        return key

    @staticmethod
    def _warn_if_writable(path: str) -> None:
        """Warn at startup if the active BAO token can overwrite the chain key.

        Write access (create/update) on the chain key path lets any holder of
        this token silently replace the key and forge a valid hash chain.  The
        Jeli process token should be read-only; see docs/key-management.md.

        Checks both the user-facing KV path and the KVv2 data path (e.g.
        secret/jeli-chain-key → secret/data/jeli-chain-key) because OpenBAO
        policies are written against the data path while the kv subcommand
        accepts the user-facing path.  "root" capability implies all access.

        Best-effort: any subprocess failure is swallowed so it never blocks startup.
        """
        # Check both KVv1-style path and KVv2 data path so policy grants are caught
        # regardless of how the mount was configured.
        paths_to_check = [path]
        parts = path.split("/", 1)
        if len(parts) == 2:
            paths_to_check.append(f"{parts[0]}/data/{parts[1]}")

        for check_path in paths_to_check:
            try:
                out = subprocess.run(  # nosec B603 B607 — shell=False, fixed argv; check_path derives from an already-validated path
                    ["bao", "token", "capabilities", check_path],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=True,
                )
                caps = {c.strip().lower() for c in out.stdout.split(",")}
                if "root" in caps:
                    logger.warning(
                        "OpenBAO root token is being used for Jeli — root has unrestricted "
                        "write access to the chain key path %r; provision a read-only token "
                        "(see docs/key-management.md)",
                        path,
                    )
                    return
                write_caps = caps & {"create", "update", "delete"}
                if write_caps:
                    # only capability names (e.g. "create,update") and the KV path are
                    # logged below — never the token value itself
                    logger.warning(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
                        "OpenBAO token has write access (%s) to chain key path %r — "
                        "anyone holding this token can replace the key and forge records; "
                        "provision a read-only token for Jeli (see docs/key-management.md)",
                        ", ".join(sorted(write_caps)),
                        path,
                    )
                    return
            except Exception:  # nosec B110  # noqa: BLE001 — capability check is best-effort
                pass


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
