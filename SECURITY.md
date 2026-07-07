# Security Policy

Jeli is a security and governance layer for personal memory systems — its entire
reason to exist is to make memory trustworthy, verifiable, and hard to poison.
This document describes the threat model it is built against, the mechanisms that
defend the store, their known limits, and how to report a vulnerability.

It is written for developers evaluating whether to use or contribute to Jeli.
Where a defense has a gap, the gap is stated plainly rather than hidden.

---

## 1. Threat model

As of 2026, memory-poisoning attacks against AI agents are documented and active:

| Attack | What it does | How Jeli responds |
|---|---|---|
| **MINJA** (arXiv 2025) | Adversarial content is written into long-term memory so it hijacks the agent's behavior at recall time. 95%+ injection success, 70%+ attack success under realistic conditions. | The injected record is captured with a **low, foreign trust score** and, if it carries injection markers, is **flagged and trust-capped at 0.3** at write time. Retrieval wraps flagged content in a quarantine envelope so it is never silently fed back to the agent as fact. |
| **Microsoft "AI Recommendation Poisoning"** (Feb 2026) | At-scale manipulation of what a memory system recommends, by seeding it with attacker-favorable "facts". | **Agent-declared trust is clamped** to the agent ceiling (0.6) at the MCP boundary — an agent cannot assert user-grade (1.0) authority for content it ingested. The Constitutional **WriteGate** can additionally cap or deny whole content-classes. |
| **Palo Alto Unit 42 IJPI** (indirect prompt injection) | A document or web page carries hidden instructions that poison the agent's memory persistently when ingested. | Content sourced externally is **server-side stigmatised** to `external-untrusted` regardless of the class the agent claimed, then screened by the **layered injection defense** (regex + optional LLM second pass). Anything that lands is **hash-chained**, so a later covert edit breaks verification. |

The unifying property: an injected memory cannot enter the store as trusted,
un-attributed fact. It either (a) breaks the hash chain if tampered after the
fact, (b) carries a low/foreign trust score that surfaces it as suspect, or
(c) is blocked outright by a user-signed constitutional rule.

Documented limitations of the integrity model live in
[docs/THREAT-MODEL.md](docs/THREAT-MODEL.md) and are summarized under each
section below.

---

## 2. Cryptographic integrity

Every memory write is appended to a per-entity **HMAC-SHA256 hash chain**. There
is no in-place UPDATE and no DELETE of content on the write path — corrections
and redactions are themselves appended events.

- **`record_hash` = HMAC-SHA256(chain_key, canonical(content + metadata + prev_hash + key_id))**
- **`prev_hash`** links each record to its predecessor; **`chain_seq`** orders the chain.
- The signing **`key_id`** is *inside* the canonical hashed form, so keys can be
  rotated without re-signing history; an unknown key fails closed.
- Writes are serialized under a Postgres advisory lock so concurrent multi-agent
  writers cannot fork the chain.
- Append-only is enforced at the **database privilege layer** (`jeli_app` role:
  INSERT + SELECT only), not merely in application code.

**`jeli verify`** walks the provenance log, recomputes every hash, and reports
the first out-of-sync record (exit 0 valid / 1 broken / 2 misconfigured;
`--json` for machines, `--report` for a full health report including the state
chain, cache consistency, and trust/queue stats).

**What this catches:** any silent overwrite, deletion, back-/post-dating, or
tampering of a stored record — including an attacker with DB write access but
without the chain key. **What it does not catch:** a write that is malicious but
well-formed and correctly signed by a holder of the chain key. Guard the chain
key like a root credential.

---

## 3. Trust model

Every write carries a trust score reflecting the authority of its source:

| Source | Trust |
|---|---|
| User direct (typed/spoken) | 1.0 |
| User confirmed (agent proposed, user approved) | 0.9 |
| Agent inferred from conversation | 0.6 |
| Agent inferred from behavior/clicks | 0.4 |
| External source (web, docs) | 0.3 |

- **Agent ceiling (0.6):** the Scoped MCP server clamps any agent-declared trust
  to `agent_trust_ceiling` (default 0.6) at dispatch. An agent physically cannot
  write user-grade trust. The Constitutional WriteGate can lower this further per
  content-class.
- **Read-time decay:** retrieval reports an `effective_trust = stored × f(age)`.
  Memories below 0.9 decay (≈1%/day); user-confirmed facts (≥0.9) do not decay.
  Decay is computed at read time — the stored score is never mutated, so the
  chain stays intact. `jeli decay-report` surfaces memories whose effective
  trust has drifted far from their stored score.

---

## 4. Constitutional layer

The Constitutional layer is the inviolable floor: **user-only**, hash-chained,
and enforced by architecture. Agents can never create, edit, or revoke a rule —
the CLI (`jeli constitutional add/list/revoke/verify`) is a user-tier surface,
not an MCP tool. Rules are retired, never deleted; `constitutional verify`
recomputes each rule's HMAC and reports any tampering.

Two gates enforce rules:

- **WriteGate** runs inside `capture_memory` **before the record is hashed**, so
  a denied write never enters the chain and any trust cap is baked into the
  attested record.
- **ReadGate** runs as the **last step of `search_memory`, after ranking**, so no
  query an agent constructs can bypass it. An unknown rule type fails *closed*
  (results untouched but logged loudly) rather than silently widening exposure.

Rule types:

| Rule | Gate | Effect |
|---|---|---|
| `deny_write_memory_type` | Write | Reject writes of a given memory type outright |
| `max_trust_for_content_class` | Write | Cap trust for a whole content class (e.g. external ≤ 0.3) |
| `exclude_memory_type` | Read | Drop a memory type from results |
| `exclude_content_class` | Read | Drop a content class from results |
| `exclude_tag` | Read | Drop results carrying a tag |
| `min_trust_floor` | Read | Hide results below an effective-trust floor |
| `max_results` | Read | Cap the number of results returned |

Rules honor `applies_to` scoping so a constraint can target a specific agent or
`all`.

---

## 5. Injection defense (layered)

Injection screening runs on the capture path in two layers:

- **Layer 1 — regex pattern matching (always on).** Detects jailbreak prefixes,
  override attempts ("ignore previous instructions"), and instruction-boundary
  markers (`<system>…</system>`). Content that matches is flagged and
  trust-capped at `FLAGGED_TRUST_CEILING` (0.3). An authoritative source
  (trust ≥ 0.9) whose content is legitimately *about* injection — e.g. a
  security note — is preserved with a recorded override reason instead of being
  capped, so the store can hold security documentation without self-poisoning.
- **Layer 2 — LLM second-pass classifier (optional).** An async second pass that
  catches natural-language evasions the regex misses. It is **opt-in** (the
  `[llm]` extra), **fails open** on any error including a missing package (a
  classifier outage never blocks a legitimate write), and is **skipped for
  trusted sources** (trust ≥ `LLM_CLASSIFIER_TRUST_SKIP` = 0.8) since those are
  already above the risk band worth the extra round-trip.

At retrieval time, flagged content is wrapped in a `<jeli:quarantine>` envelope
(or `<jeli:reference>` with an override reason for authoritative security-doc
content). The wrapper is applied at read time and is never stored, so it cannot
itself be chained or tampered.

**Known gap (GitHub [issue #33](https://github.com/LegionForge/jeli/issues/33)):**
the Layer-1 regex is keyword-oriented and can be evaded by unicode homoglyphs,
whitespace injection, or a sufficiently reworded instruction. This is documented
honestly in the adversarial test suite (`tests/test_adversarial_eval.py`), which
asserts the false negatives explicitly rather than papering over them. The Layer-2
LLM classifier exists specifically to close this gap; it is not enabled by default
because it adds a dependency and a per-write LLM call.

---

## 6. API key security

- **Comparison:** the server auth key is checked with `hmac.compare_digest()`,
  never `==` / `!=` — constant-time, to deny a timing oracle.
- **Generation:** `secrets.token_urlsafe(32)` — 256 bits of entropy, URL-safe.
- **Transmission:** the key travels in the `X-API-Key` HTTP header, never a query
  parameter (query strings leak into logs and referrers).
- **Storage:** config files holding the API key / chain key should be `chmod 0600`;
  the server warns at startup if permissions are too open.
- **Pre-TLS LAN cleartext** is a known deployment gap — mitigate with
  network-level controls (VLAN, firewall, or an SSH tunnel) until TLS is in front
  of the server.

---

## 7. Reporting a vulnerability

**Please report security vulnerabilities privately. Do not open a public GitHub
issue for a security bug.**

- Preferred: [GitHub private vulnerability reporting](https://github.com/LegionForge/jeli/security/advisories/new)
- Or email: **jp@legionforge.org**

Please include:

- a description of the vulnerability,
- reproduction steps (and affected version/commit),
- the potential impact.

You should receive an acknowledgement within **72 hours**. Reports that reduce to
a limitation already documented in [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md)
are still welcome — especially with a novel exploitation path.

**Supported version:** the latest release on `main`.
