# Jeli Threat Model — v0.1

Honest statement of what the v0.1 integrity layer does and does not guarantee.
Overclaiming is worse than the gap: agents and users will calibrate their
trust to this document.

## What v0.1 guarantees

| Property | Mechanism |
|---|---|
| Post-write content tampering is detectable | HMAC-SHA256 hash chain over the canonical record (`jeli verify` finds the first bad record) |
| Records cannot be silently reordered or removed mid-chain | prev-hash linkage breaks on any resequencing |
| A record cannot be re-pointed at a weaker signing key | `key_id` is inside the hashed canonical form |
| Key rotation without re-signing history | per-record key registry; unknown `key_id` fails closed |
| Agents cannot impersonate other writers | actor identity is server-side config, not a tool argument |
| Injection-styled content cannot claim authority | write-path pattern match caps trust at 0.3 and flags it; the flag is returned at read time |
| History cannot be rewritten by the app itself | `jeli_app` DB role holds INSERT+SELECT only (scripts/setup_db_roles.sql) |
| Retiring/reviving memories requires the chain key | state changes are hash-chained events; column cache is cross-checked by `jeli verify` |
| Concurrent writers cannot fork the chain | chain writes serialize under a Postgres advisory lock |

## What v0.1 does NOT guarantee — known gaps

**Poisoning at write time is flagged, not prevented.** A MINJA-style attack
writes through the legitimate path and receives a perfectly valid hash. The
defenses are heuristic (pattern flagging, trust capping, provenance for later
revocation) — not cryptographic. Jeli v0.1 is *poison-auditable*, not
poison-proof. Collusive multi-record poisoning and consolidation-skew
(flooding) attacks are out of scope for v0.1 entirely.

**Temporal fields — CLOSED as of 006.** Every supersession/invalidation is
recorded in `memory_state_event`'s own HMAC chain; the mutable columns are a
cache whose authority is the event chain, and `jeli verify` cross-checks
them — hiding or resurrecting a memory by flipping columns without the
chain key is detected. State changes are user-tier operations (`jeli
revise` / `jeli invalidate`, never MCP tools); the `jeli_user` role holds
COLUMN-level UPDATE grants only (temporal columns — content remains
structurally unwritable). Residual: an attacker holding BOTH admin DB
access and the chain key can still forge state events — same residual as
the memory chain itself (see chain-key custody below).

**Chain-key compromise defeats verification.** An attacker holding both DB
write access and the chain key can rewrite and re-sign everything. Planned
fix: keys held in a vault (OpenBAO transit — Jeli requests signatures, never
holds key material) plus periodically anchored chain-head checkpoints stored
outside the database's blast radius. Until then: the chain key is a
root-grade credential; do not keep it in `.env` files on shared machines.

**The audit log is append-only by grants, not by cryptography.** Audit rows
are not yet hash-chained; an admin-level attacker can delete them silently.

**Search results are a prompt-injection channel.** `search_memory` returns
memory content into an agent's context. Consumers MUST treat results as
untrusted data, not instructions. The `injection_flagged` field exists so
callers can quarantine, but unflagged content is not certified safe.

## Red-team findings (2026-07-07) — remediated

An adversarial audit of the v0.2.0-alpha poisoning defenses confirmed all
three read/write defenses are correctly coded but were **surface-specific**:
other read and write paths did not inherit them. All findings are now fixed;
read-time defenses are applied through a single `apply_read_defenses` /
`wrap_for_read` choke point that every read surface calls.

| Issue | Severity | Gap | Status |
|---|---|---|---|
| #35 | HIGH | Caller metadata not whitelisted; an agent could set `content_class=security-doc` + fake `trust_override_reason` to downgrade the quarantine wrap, or forge `insight_type`/`is_session_summary` to impersonate daemon output | FIXED: `SERVER_OWNED_METADATA_KEYS` stripped at the MCP boundary |
| #36 | HIGH | `search_by_entity` returned content raw, with no read-time wrap and no trust decay | FIXED: routed through `apply_read_defenses` |
| #37 | HIGH | Importer applied no trust ceiling and passed metadata through; a crafted archive could launder trust to 1.0, spoof security-doc, and weaponize the resolver | MITIGATED: import trust ceiling (default 0.3) + metadata strip + user-tier tie escalation guard. Crypto source-verification is the tracked long-term fix |
| #38 | MEDIUM | Safety-aware re-ranking ran only on `rerank=true` semantic calls | FIXED: unconditional on semantic; flag demotion added to fts ordering |
| #39 | MEDIUM | Synthesized cluster insights stored unwrapped | FIXED: `<jeli:derived>` wrap when `source_trust_min` < floor |
| #40 | LOW | `audit_trail` returned content unwrapped and omitted `injection_flagged` | FIXED: flag surfaced; flagged content wrapped |

The root lesson, now applied: read-time defenses live at a single choke point
(`apply_read_defenses`), not re-implemented per surface.
