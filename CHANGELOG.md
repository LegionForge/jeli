# Changelog

## Unreleased

Post-v0.2.0-alpha hardening: the OpenBAO signing-oracle key path made real,
import trust made provenance-aware, and a run of security-critical DB-privilege
and injection-detection fixes.

### Added
- **OpenBAO write-guard** (#42): warns at startup if the OpenBAO token can
  write the chain key (checks KVv1 and KVv2 capability paths; flags root
  tokens explicitly). Ships the required read-only policy HCL and token
  creation command.
- **HMAC-verified import trust** (#42, GH #41): `MemoryImporter` verifies
  `record_hash` + `key_id` against the local chain key; own-store records
  import at their original trust, unverified records stay clamped to the
  import ceiling (GH #37 default-deny intact).
- **Judicial precedent corroboration / Sybil gate** (#45, alembic 016):
  precedent confidence grows only on *distinct* corroborating sources
  (`source_agent`), not raw agreement count — repeat agreement from one actor
  grows `applied_count` only. A single actor flooding one resolution stays
  pinned at base confidence; crossing the apply threshold needs independent
  sources. Every precedent *overturn* is now surfaced to the human-escalation
  queue (the overturn-vs-corroboration-ledger policy is deliberately left to
  human review pending real-world observation).

### Fixed
- **Non-env key providers were unusable at real startup** (#43): `get_settings()`
  validated the chain key before `__main__` resolved it through the configured
  provider, so any non-`env` provider (openbao, keychain, 1password, passphrase,
  file) failed closed on an empty key. `scripts/run_mcp_stdio.sh` also only read
  a file-based key; added a `JELI_KEY_PROVIDER=openbao` mode so the cutover
  actually reaches the running MCP server.
- **Column-grant gaps in the append-only privilege model** (#45, #49): a column
  named in an `UPDATE ... SET` needs privilege even when its value doesn't
  change (Postgres checks syntactically). Three statements referenced columns
  the grants omitted and had been failing closed against the real `jeli_app`
  role: judicial `record()` (since alembic 014 — `judicial_precedent` was empty
  in production as a result; fixed in alembic 016), `jeli reembed`
  (`embedding_model`/`dimensions`/`embedded_at`), and entity `record_relation()`
  (`confidence`) — the last two fixed in alembic 017.
- **Injection-regex false positives** (#46, GH #33): bare `bypass`/`override`/
  `instead of`/mid-text `system:` fired on ordinary technical prose (bug
  reports, changelogs, `compose.override.yaml`), producing false inbox holds.
  `bypass`/`override` now require a nearby possessive aimed at the AI, `system:`
  is anchored to the message start, and `instead of` is dropped. The inbox
  worker also now attributes a hold to `regex_injection` vs `llm_classifier`
  correctly instead of always labeling it the latter.

### Security
- **CLI flag-injection hardening** (#42): `_reject_flag_like` guards CLI
  arguments; Bandit/Semgrep CI findings cleared.
- LLM injection-classifier prompt tightened to distinguish text that *issues*
  an instruction to an AI from text that merely *describes* one (#46).

### Known / open
- **Judicial precedent semantics** (#50, non-blocking): precedent is recorded
  and reinforced but never actually applied to change a resolution, and a
  precedent at/above the apply threshold (0.7) can never be overturned (the
  erosion path is unreachable once settled). Both are pre-existing design
  gaps awaiting a semantics ruling; documented, not yet changed.

## v0.2.0-alpha (2026-07-06)

The three-branch governance model lands: a user-signed Constitutional layer,
a Judicial precedent system, and the poisoning defenses (LLM second-pass
classifier, entity graph, portability) that make the store both harder to
corrupt and impossible to lock in.

### Added
- **Constitutional layer** (alembic 013): user-signed, hash-chained rules that
  no agent can override. `WriteGate` denies writes by `memory_type` or caps
  trust by `content_class` before a record is hashed; `ReadGate` filters what
  leaves the store. `jeli constitutional add/list/revoke/verify` (rules are
  retired, never deleted; tampering is detectable).
- **Judicial precedent system** (alembic 014): settled conflicts become case
  law the conflict resolver consults; unresolvable conflicts go to a human
  escalation queue. `jeli judicial precedents/pending/resolve`.
- **Entity graph** (alembic 015): `EntityExtractor` runs on every capture;
  `GraphStore` backs two new MCP tools (`search_by_entity`, `get_entity_graph`)
  and `jeli graph entities/search/relations`.
- **Memory portability**: `jeli export` / `jeli import` stream the store as a
  JSON-Lines archive (optional audit trail, trust/type filters, `--dry-run`).
  Sovereignty-preserving: move your memories out with no vendor lock-in.
- **LLM injection classifier** (GH #33): opt-in async second pass after the
  regex screen, catching natural-language evasions the patterns miss. Fails
  open, skips authoritative sources, gated behind the `[llm]` extra.
- **Insights daemon cluster synthesis**: the nightly cluster scan can
  synthesize a summary memory per semantic cluster via an LLM (degrades to a
  non-LLM summary when unavailable).
- **Contradiction surfacing**: conflicts the resolver cannot settle are
  surfaced rather than silently dropped.
- `jeli verify --report`: full integrity health report (chain + state-chain
  validity, cache consistency, memory/trust/queue stats) as JSON.
- Server-side `content_class` stigmatisation: externally-sourced content is
  forced to `external-untrusted` regardless of what the agent claimed.
- Integration-test infrastructure: `docker-compose.test.yml` +
  `scripts/run_integration_tests.sh` for one-command local live-Postgres runs
  (`JELI_TEST_DB_PORT` overrides the host port when 5433 is taken).
- **Unicode normalization pre-pass** (GH #33, homoglyph half): injection
  detection folds zero-width characters, fullwidth forms, and Cyrillic/Greek
  confusables before pattern matching. Detection-only; stored content is
  never altered.
- `constitutional verify` now re-signs **revoked rules too**: retired
  history stays tamper-evident (`load_all_rules`, `revoked_checked` count).
- **Judicial case-law semantics**: a dissenting deliberation no longer
  overwrites settled precedent: agreement reinforces, dissent erodes
  confidence, and only sustained dissent below `OVERTURN_FLOOR` (0.3)
  overturns the resolution.
- **docs/ARCHITECTURE.md**: code-level architecture: module map, the
  write/read/verify paths, data model by migration, trust model.
- **CodeTour walkthroughs** (`.tours/`): three guided in-editor tours for
  VS Code: the write path, governance, and integrity/verify. Steps anchor on
  code patterns, not line numbers.
- **Anti-laundering trust inheritance**: cluster-synthesized insights now
  inherit `min(source trusts)` (capped at 0.5), exclude injection-flagged
  members from synthesis input, and record `derived_from` lineage. Closes
  the consolidation laundering channel (MemLineage/TMA-NM pattern).
- **Unverified-procedure wrapping**: procedural memories below effective
  trust 0.7 get a read-time `<jeli:unverified-procedure>` do-not-imitate
  envelope (MemoryGraft defense).
- **Safety-aware re-ranking**: `rerank=true` now applies a deterministic
  trust/flag penalty after relevance scoring, so engineered similarity
  cannot outrank provenance.
- **Red-team remediation** (2026-07-07 audit, issues #35-#40): read-time
  defenses consolidated into a single `apply_read_defenses` / `wrap_for_read`
  choke point that every read surface calls. Server-owned provenance/security
  metadata keys are stripped from agent input at the MCP boundary (#35);
  `search_by_entity` now applies decay + wrapping (#36); the safety penalty
  runs on all semantic searches, not just `rerank=true`, and flagged content
  is demoted in fts ordering (#38); low-provenance synthesized insights get a
  `<jeli:derived>` wrap (#39); `audit_trail` surfaces `injection_flagged` and
  wraps flagged content (#40). The portability importer clamps imported trust
  to a ceiling (default 0.3, `--trust-ceiling` to override) and strips
  server-owned metadata, and the conflict resolver escalates rather than
  auto-invalidating a user-tier memory on a recency tie (#37).

### Changed
- `capture_memory` runs the write path through the Constitutional `WriteGate`
  and the optional LLM classifier before hashing.

### Security
- GH #33: documented the regex injection detector's natural-language evasion
  gap and closed it with the opt-in LLM second-pass classifier.

## v0.1.1 (2026-07-04)

### Added
- Hash-chained state events (alembic 006): `jeli revise` / `jeli invalidate`
  (user-tier, never MCP tools); temporal columns become a verified cache;
  `jeli verify` walks both chains and cross-checks. Closes the
  THREAT-MODEL temporal-fields gap.
- `jeli_user` DB role: column-level UPDATE grants (temporal fields only);
  content structurally unwritable even for the user tier
- Markdown import pipeline (`scripts/import_markdown.py`) + search
  benchmark (`scripts/bench_search.py`); asymmetric query prefixes
- `SCOPED_MCP_EMBED_KEEP_ALIVE` (default 30m): keeps the embed model
  resident; eliminates the multi-second after-idle cold start on the
  first query. (Measured honestly: steady-state p50 is unchanged at
  ~160ms; that cost is arctic-embed2 inference itself. Future levers:
  query-embedding cache, faster query encoder.)
- Semantic search: pgvector `vector(1024)` + HNSW cosine index (alembic
  004); `search_memory` mode `semantic` returns per-hit `distance`
- Ollama embedding provider implemented (`/api/embed`) and made the
  default (local-first); default model `snowflake-arctic-embed2`
- OpenAI provider truncates to the 1024 index standard via the
  `dimensions` parameter (matryoshka)
- Write-path dimension guard: non-1024 embeddings are refused so the
  index can never silently mix dimensions

### Changed
- Default embedding provider `openai` → `ollama` (sovereignty default)

## v0.1.0-alpha (2026-07-02)

First working release: a cryptographically auditable memory store with a
scoped agent API.

### Added
- Scoped MCP server (stdio) exposing exactly four tools: `capture_memory`,
  `search_memory` (fts), `audit_trail`, `verify_chain`
- HMAC-SHA256 hash chain with per-record signing-key identity (`key_id`
  inside the canonical hashed form; rotation without re-signing history;
  unknown keys fail closed)
- Trust-scored writes (user-stated 1.0 … external 0.3); injection-styled
  content capped at 0.3 and flagged, at write time and in search results
- Server-side actor identity (agents cannot impersonate writers)
- Chain writes serialized under a Postgres advisory lock (concurrent
  multi-agent writers cannot fork the chain)
- Append-only enforced at the DB privilege layer (`jeli_app` role:
  INSERT+SELECT only; scripts/setup_db_roles.sql)
- `jeli verify` CLI (exit 0 valid / 1 broken / 2 misconfigured, `--json`)
- Alembic schema (memory_entry, memory_audit_log, memory_contradiction),
  embeddings stored as JSONB with full provenance (model, dims, timestamp)
- Threat model documenting guarantees and known gaps (docs/THREAT-MODEL.md)
- CI via dev-rig reusable workflows + live-Postgres integration job;
  pre-push history-scrub hook (.githooks/pre-push)

### Known limitations (see THREAT-MODEL.md)
- Semantic search lands with the pgvector migration; fts (substring) only
- Temporal invalidation fields are not yet integrity-protected
- Poisoning at write time is flagged/audited, not prevented
