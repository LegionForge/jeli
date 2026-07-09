# Chain-Key Management

Jeli's hash chain is keyed by a single HMAC secret, the **chain key**. It is a
root-grade credential: anyone holding it can forge records that verify (see
[trust-doctrine.md](trust-doctrine.md) and [THREAT-MODEL.md](THREAT-MODEL.md)).
Where that key comes from is pluggable, selected by `SCOPED_MCP_KEY_PROVIDER`
and resolved once at startup into `chain_key`.

## Two tiers of provider

There is a fundamental split, and it drives the whole design:

- **Key-material providers** *return* the key bytes, which then flow through
  Jeli's existing local HMAC path unchanged. Simple, synchronous, and covers the
  common cases. **Implemented today.**
- **Signing-oracle providers** *never release* the key; you send a payload and
  get a MAC back (the key stays in a vault, HSM, or hardware token). This is
  strictly stronger (a memory-scraping attacker never sees the key) but it needs
  an async signing path, so it is the tracked next step, not yet built.

## Implemented: key-material providers

| `SCOPED_MCP_KEY_PROVIDER` | `SCOPED_MCP_KEY_REF` | Notes |
|---|---|---|
| `env` (default) | unused | Key from `SCOPED_MCP_CHAIN_KEY`. Historical behaviour, unchanged. |
| `file` | path | Read from a dedicated file, e.g. `~/.jeli-secrets/chain_key`. Warns if the file is group/world readable. |
| `keychain` | service name (default `jeli-chain-key`) | OS keychain via the `keyring` extra (`pip install -e ".[keychain]"`), or the macOS `security` CLI as a fallback. Account is the chain key id. |
| `1password` | `op://Vault/Item/field` | Reads via the `op` CLI (must be installed and signed in). |
| `openbao` | `<kv-path>#<field>` (field default `value`) | Reads a KV secret via the `bao` CLI. Uses ambient `BAO_ADDR` / `BAO_TOKEN`; the vault must be unsealed and the caller authenticated. This is OpenBAO as a key *store*, not the transit signing oracle below. |
| `passphrase` | salt (hex) | Derives the key from an interactively-entered passphrase via scrypt (n=2^15). Reproducible given the same passphrase and salt. Non-interactive use reads `SCOPED_MCP_PASSPHRASE`. |

### Two ways to use OpenBAO

- **KV store (`openbao`, above): available now.** OpenBAO holds, access-controls,
  audits, and rotates the chain key; Jeli fetches it at startup. The key still
  lands in Jeli's process memory, but custody moves off the host into the vault.
- **Transit signing oracle: planned (see below).** The key never leaves the
  vault; Jeli asks OpenBAO to compute the MAC. Strictly stronger, and the
  recorded near-term target, but it needs the async `Signer` refactor.

Generate a salt for the passphrase provider:

```bash
python -c 'import secrets; print(secrets.token_hex(16))'
```

The default (`env`) is a no-op passthrough, so nothing changes unless a provider
is explicitly selected.

## Planned: signing-oracle providers (Tier 2)

These need a `Signer` contract (`mac(payload)` / `verify(payload, mac)`, both
async) and a refactor of the synchronous `compute_record_hash` path. Documented
here so the design target is explicit:

- **OpenBAO / HashiCorp Vault transit**: HMAC generate/verify over the API; the
  key never leaves the vault. The recorded near-term target.
- **Cloud KMS**: AWS KMS, GCP KMS, Azure Key Vault (MAC/sign via API).
- **PKCS#11 HSM**: YubiHSM, Nitrokey HSM, cloud HSM.
- **FIDO2 `hmac-secret`**: a hardware authenticator (e.g. YubiKey) derives a
  symmetric secret bound to the physical token. This is the natural home for the
  "FIDO2 key" idea, where the chain key never exists without the token present.
- **TPM 2.0**: sealed keys bound to platform state.
- **Threshold / MPC signing**: no single holder can produce a MAC alone; the
  real answer to "no single point of failure," and it pairs with succession
  (heirs hold shares).

## Also worth considering (Tier 1 extensions)

- **SOPS**: encrypted files with age/PGP/KMS backends; decrypt to key material.
- **age-encrypted keyfile**: modern, simple, decrypt with an age identity.
- **GPG-encrypted keyfile**: the classic PGP pattern.

## A clarification on OIDC / OpenID

OpenID Connect is an *authentication* protocol (it proves *who a principal is*),
not a key store or a signing device. It does not hold or derive the chain key,
so it does not belong in this module. It is highly relevant to a *different*
thread: identity-bound writes and RBAC, where a principal (a human, or a named
agent) is authenticated before a write or a scoped read. Keep the two separate:
**key custody** (this document) answers "can this MAC be forged"; **principal
identity** (OIDC, mTLS, capability tokens) answers "who is asking, and what may
they do."

## Reads survive key loss (invariant to preserve)

Reading memories does not require the chain key; only writing new records and
running verification does. Losing the key should therefore stop new signed
writes and integrity checks, never lose access to the stored memory. Any future
custody design must preserve this, so that key loss is recoverable rather than
catastrophic.
