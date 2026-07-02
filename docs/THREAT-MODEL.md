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
| Concurrent writers cannot fork the chain | chain writes serialize under a Postgres advisory lock |

## What v0.1 does NOT guarantee — known gaps

**Poisoning at write time is flagged, not prevented.** A MINJA-style attack
writes through the legitimate path and receives a perfectly valid hash. The
defenses are heuristic (pattern flagging, trust capping, provenance for later
revocation) — not cryptographic. Jeli v0.1 is *poison-auditable*, not
poison-proof. Collusive multi-record poisoning and consolidation-skew
(flooding) attacks are out of scope for v0.1 entirely.

**Temporal fields are not integrity-protected.** `valid_until`,
`superseded_by`, and `amended_from` are set after write, so they cannot live
inside the write-time hash. An attacker with admin DB access can resurrect a
retracted memory or hide a live one without breaking any hash. Planned fix:
supersession/amendment become hash-chained append events (the audit trail
becomes the authority for state). Until then, mitigations are the restricted
`jeli_app` role (the app cannot do it) and DB-access hygiene.

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
untrusted data, not instructions — the `injection_flagged` field exists so
callers can quarantine, but unflagged content is not certified safe.
