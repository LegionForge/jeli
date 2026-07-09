# Jeli: Code Architecture

This is the code-level companion to the conceptual three-branch governance
model in the [README](../README.md). It maps every module to its governance
role and walks the three paths that matter: **write**, **read**, and
**verify**.

For the threat model and defense layers, see [SECURITY.md](../SECURITY.md).
For a guided in-editor walkthrough, install the
[CodeTour](https://marketplace.visualstudio.com/items?itemName=vsls-contrib.codetour)
VS Code extension and open the tours in [`.tours/`](../.tours/).

---

## Module map

```
src/jeli_scoped_mcp/
├── server/
│   └── mcp_server.py      MCP surface: the ONLY door agents get.
│                          Dispatch, server-side trust clamp, content-class
│                          inference, ReadGate application on results.
├── tools/
│   ├── memory_tools.py    capture_memory / search_memory / audit_trail;
│   │                      the write path lives here end to end.
│   └── state_tools.py     Chained state events: invalidate, revise, redact.
│                          Never UPDATE-in-place; every change is an append.
├── core/
│   ├── hash_chain.py      Canonical JSON, HMAC-SHA256 record hashing,
│   │                      chain validation, amendment tracking.
│   ├── trust_score.py     Trust tiers, validation/clamping, read-time decay.
│   └── contradiction.py   Contradiction detection + severity classification.
├── security.py            APIKeyValidator (timing-safe), InjectionDefense:
│                          regex layer + unicode normalization pre-pass +
│                          opt-in LLM second-pass classifier.
├── constitutional/        CONSTITUTIONAL branch (user-only, inviolable)
│   ├── rules.py           ConstitutionalRule dataclass, canonical form,
│   │                      HMAC signing.
│   ├── manager.py         Add / list / revoke / verify rules. TTL cache.
│   │                      Verification covers revoked rules too.
│   └── gate.py            WriteGate (pre-hash, can block or cap trust) and
│                          ReadGate (filters every search result set).
├── judicial/              JUDICIAL branch (conflict resolution)
│   ├── precedent.py       Case law: pattern-hashed precedents with
│   │                      agreement-reinforces / dissent-erodes /
│   │                      sustained-dissent-overturns semantics.
│   └── escalation.py      HumanEscalationQueue: unresolvable conflicts
│                          surface to the user (appellate process).
├── daemons/
│   ├── conflict_resolver.py  Judicial enforcement: pg_notify-driven,
│   │                         arbitrates HIGH conflicts via trust + precedent.
│   ├── insights.py           Consolidation, cluster synthesis, contradiction
│   │                         surfacing (the "dreaming" loop).
│   ├── maintenance.py        Archive expired, prune inbox, cache upkeep.
│   └── runner.py             Supervised daemon lifecycle.
├── inbox/                 Ingestion Bouncer (staging before the chain)
│   ├── models.py          ClassifierDecision, status/urgency/durability enums.
│   ├── classifier.py      Dedup, importance/urgency scoring, entity hints.
│   └── worker.py          InboxWorker×N, FOR UPDATE SKIP LOCKED queue.
├── graph/                 Entity graph (auto-extracted on capture)
│   ├── extractor.py       Gazetteer + regex entity/relation extraction.
│   └── store.py           entity / memory_entity_link / entity_relation.
├── portability/           Sovereignty: leave without losing anything
│   ├── exporter.py        JSON-Lines archive (no raw vectors), per-record
│   │                      SHA-256, chain-validity attestation in manifest.
│   └── importer.py        Re-import with tamper detection; re-embeds and
│                          re-chains locally.
├── embedding/provider.py  Ollama (local-first) / OpenAI (opt-in, truncated
│                          to the 1024 index standard) / MLX.
├── reranker/provider.py   Optional LiteLLM re-ranking of search results.
├── database/pool.py       asyncpg pool + advisory-locked transactions.
├── config.py              Settings (env-driven, SCOPED_MCP_* vars).
└── cli.py                 User-tier surface: verify, re-embed, decay-report,
                           constitutional / judicial / graph / export / import.
```

**Governance mapping:** agents (Executive) reach only `server/mcp_server.py`;
storage (Legislative) is Postgres reached through `tools/` and `database/`;
`daemons/conflict_resolver.py` + `judicial/` are the Judicial branch;
`constitutional/` is the user-signed floor every other branch obeys. The CLI
is user-tier: constitutional amendments, redaction, and verification are
deliberately **not** MCP tools.

---

## The write path (capture_memory)

Every memory an agent proposes passes through this gauntlet, in order.
A failure at any step means nothing lands in the chain.

```
agent → MCP dispatch → clamp trust → infer content class
      → injection defense (regex + unicode fold, then opt-in LLM pass)
      → Constitutional WriteGate (block / cap, BEFORE hashing)
      → embed + dimension check (1024 index standard)
      → advisory lock → prev_hash read → HMAC-SHA256 → INSERT
      → audit log row (same transaction)
      → entity extraction (best-effort, never fails the write)
```

1. **Server-side trust clamp** (`mcp_server.py::_clamp_trust`). Trust is
   *caller-declared* but not caller-controlled: agent writes are clamped to
   the agent ceiling (0.6). An agent cannot claim user-tier authority.
2. **Content-class inference** (`mcp_server.py::_infer_content_class`).
   Web-shaped content written as "general" by an agent is stigmatized to
   `external-untrusted` server-side.
3. **Injection defense** (`security.py`). Regex patterns run over a
   detection-only unicode normalization (zero-width strip, NFKC fold,
   Cyrillic/Greek confusable map). Flagged content is trust-capped at 0.3,
   never blocked, because blocking would teach an attacker what evades. The
   opt-in LLM second pass covers natural-language rephrasing (GH #33).
4. **Constitutional WriteGate** (`constitutional/gate.py::WriteGate.check`).
   User-signed rules can deny the write or cap its trust. Runs **before**
   the record is hashed, so a blocked write leaves no trace to clean up.
5. **Embedding + dimension check**: the index standard is `vector(1024)`;
   a provider emitting anything else is refused at capture time.
6. **Advisory-locked chain append** (`memory_tools.py::capture_memory`).
   The `prev_hash` read and INSERT are serialized under a Postgres advisory
   lock; without it, concurrent writers fork the chain. The record hash is
   `HMAC-SHA256(chain_key, canonical(record) + prev_hash)`.
7. **Audit row**: written in the same transaction; every write carries
   actor, session, trust, and flag state.
8. **Entity extraction** (`graph/extractor.py`). Entities and relations
   are linked after commit; extraction failure never fails a capture.

The **Ingestion Bouncer** (`inbox/`) is the optional staging tier in front
of this: writes land in `memory_inbox`, the `IngestionClassifier` scores
and dedups them, and `InboxWorker` instances (safe to run N in parallel via
`FOR UPDATE SKIP LOCKED`) promote approved rows through the same
`capture_memory` gauntlet.

## The read path (search_memory / search_by_entity)

Search modes: semantic (pgvector HNSW), FTS (real `tsvector`), SQL
(whitelisted columns only). Results then pass, in order:

1. **Read-time trust decay** (`core/trust_score.py`): effective trust =
   stored trust decayed by age and type-specific half-life. Stored values
   are never rewritten.
2. **Constitutional ReadGate** (`constitutional/gate.py::ReadGate.apply`):
   active rules filter what each actor may see (exclude types/classes/tags,
   trust floors, result caps). Applies to *every* read surface, including
   `search_by_entity`. Unknown rule types fail closed.
3. **Quarantine wrapping** (read-time, never stored): injection-flagged
   content is wrapped in `<jeli:quarantine>`; authoritative security docs
   in `<jeli:reference>` with the recorded override reason.
4. **Optional re-ranking** (`reranker/`).

## The verify path (jeli verify)

`cli.py::_run_verify` walks the whole chain oldest-first
(`core/hash_chain.py::HashChainValidator.validate_chain`), recomputing every
HMAC under the record's own `key_id` (key rotation never invalidates
history) and comparing `prev_hash` links. It reports the **first**
out-of-sync record. `jeli verify --report` adds state-chain validity, cache
consistency, and trust/queue statistics. `jeli constitutional verify`
re-signs every rule, revoked rules included, since retired history must
stay tamper-evident.

Facts never delete: `state_tools.py` appends chained state events for
invalidate / revise / redact. A redaction zeroes content at read time but
the original hash stays in the chain, so the ledger remains complete.

---

## Data model (tables by migration)

| Migration | Tables / change | Branch |
|---|---|---|
| 001–006 | `memory_entry` (hash-chained, `chain_seq` order), `memory_audit_log`, `memory_state_event`, pgvector 1024 | Legislative |
| 007 | `memory_inbox` staging | Bouncer |
| 008/010 | `jeli_app` role: append-only, column-scoped exceptions | Legislative |
| 009 | `content_class` column | Security |
| 011 | redaction as chained state event | Legislative |
| 012 | real FTS (`tsvector` + GIN) | Read path |
| 013 | `constitutional_rules` (no DELETE for `jeli_app`) | Constitutional |
| 014 | `judicial_precedent`, `judicial_human_queue` | Judicial |
| 015 | `entity`, `memory_entity_link`, `entity_relation` | Graph |

The DB role model is itself a defense layer: `jeli_app` (what the MCP
server runs as) has no UPDATE/DELETE on chained tables; append-only is
enforced by Postgres grants, not application discipline. See
`scripts/setup_db_roles.sql` and `tests/integration/test_role_privileges.py`.

## Trust model (two axes)

| Source | Trust | |
|---|---|---|
| User direct | 1.0 | ground truth |
| User confirmed | 0.9 | agent proposed, user approved |
| Agent inferred (conversation) | 0.6 | **server-side agent ceiling** |
| Agent inferred (behavior) | 0.4 | needs corroboration |
| External / flagged | 0.3 | injection cap + external floor |

The second axis is `content_class` (`general`, `security-doc`,
`code-sample`, `external-untrusted`): an authoritative source (≥0.9)
writing a `security-doc` *about* injection keeps its trust with a recorded
override reason, so the store can hold security documentation without
self-poisoning.

## Judicial case-law semantics

`ConflictResolverDaemon` wakes on a `pg_notify` INSERT trigger, claims
conflict rows (`FOR UPDATE SKIP LOCKED`), and resolves HIGH-severity
contradictions deterministically: higher trust wins; newer wins on a tie.
The loser is invalidated via a chained state event, never deleted.

The precedent layer (`judicial/precedent.py`) turns repeated rulings into
case law, keyed by a symmetric pattern hash (contradiction type + the two
memory types):

- **Agreement** grows `applied_count` and confidence (+0.1/step, ceiling 1.0)
- **Dissent** keeps the standing resolution and erodes confidence one step;
  a single disagreement never rewrites settled law
- **Sustained dissent** below `OVERTURN_FLOOR` (0.3) overturns the precedent:
  the new resolution takes over at base confidence, count restarts

A precedent at confidence ≥0.7 is applied without re-deliberation.
Repeatedly conflicting memories escalate to the `HumanEscalationQueue`
(`jeli judicial pending` / `resolve`); the user is the appellate court.
