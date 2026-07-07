# Changelog

## v0.2.0-alpha — 2026-07-06

The three-branch governance model lands: a user-signed Constitutional layer,
a Judicial precedent system, and the poisoning defenses (LLM second-pass
classifier, entity graph, portability) that make the store both harder to
corrupt and impossible to lock in.

### Added
- **Constitutional layer** (alembic 013) — user-signed, hash-chained rules that
  no agent can override. `WriteGate` denies writes by `memory_type` or caps
  trust by `content_class` before a record is hashed; `ReadGate` filters what
  leaves the store. `jeli constitutional add/list/revoke/verify` (rules are
  retired, never deleted; tampering is detectable).
- **Judicial precedent system** (alembic 014) — settled conflicts become case
  law the conflict resolver consults; unresolvable conflicts go to a human
  escalation queue. `jeli judicial precedents/pending/resolve`.
- **Entity graph** (alembic 015) — `EntityExtractor` runs on every capture;
  `GraphStore` backs two new MCP tools (`search_by_entity`, `get_entity_graph`)
  and `jeli graph entities/search/relations`.
- **Memory portability** — `jeli export` / `jeli import` stream the store as a
  JSON-Lines archive (optional audit trail, trust/type filters, `--dry-run`).
  Sovereignty-preserving: move your memories out with no vendor lock-in.
- **LLM injection classifier** (GH #33) — opt-in async second pass after the
  regex screen, catching natural-language evasions the patterns miss. Fails
  open, skips authoritative sources, gated behind the `[llm]` extra.
- **Insights daemon cluster synthesis** — the nightly cluster scan can
  synthesize a summary memory per semantic cluster via an LLM (degrades to a
  non-LLM summary when unavailable).
- **Contradiction surfacing** — conflicts the resolver cannot settle are
  surfaced rather than silently dropped.
- `jeli verify --report` — full integrity health report (chain + state-chain
  validity, cache consistency, memory/trust/queue stats) as JSON.
- Server-side `content_class` stigmatisation — externally-sourced content is
  forced to `external-untrusted` regardless of what the agent claimed.
- Integration-test infrastructure: `docker-compose.test.yml` +
  `scripts/run_integration_tests.sh` for one-command local live-Postgres runs.

### Changed
- `capture_memory` runs the write path through the Constitutional `WriteGate`
  and the optional LLM classifier before hashing.

### Security
- GH #33: documented the regex injection detector's natural-language evasion
  gap and closed it with the opt-in LLM second-pass classifier.

## v0.1.1 — 2026-07-04

### Added
- Hash-chained state events (alembic 006): `jeli revise` / `jeli invalidate`
  — user-tier, never MCP tools; temporal columns become a verified cache;
  `jeli verify` walks both chains and cross-checks. Closes the
  THREAT-MODEL temporal-fields gap.
- `jeli_user` DB role: column-level UPDATE grants (temporal fields only) —
  content structurally unwritable even for the user tier
- Markdown import pipeline (`scripts/import_markdown.py`) + search
  benchmark (`scripts/bench_search.py`); asymmetric query prefixes
- `SCOPED_MCP_EMBED_KEEP_ALIVE` (default 30m): keeps the embed model
  resident — eliminates the multi-second after-idle cold start on the
  first query. (Measured honestly: steady-state p50 is unchanged at
  ~160ms; that cost is arctic-embed2 inference itself. Future levers:
  query-embedding cache, faster query encoder.)
- Semantic search: pgvector `vector(1024)` + HNSW cosine index (alembic
  004); `search_memory` mode `semantic` returns per-hit `distance`
- Ollama embedding provider implemented (`/api/embed`) and made the
  default — local-first; default model `snowflake-arctic-embed2`
- OpenAI provider truncates to the 1024 index standard via the
  `dimensions` parameter (matryoshka)
- Write-path dimension guard: non-1024 embeddings are refused so the
  index can never silently mix dimensions

### Changed
- Default embedding provider `openai` → `ollama` (sovereignty default)

## v0.1.0-alpha — 2026-07-02

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
  INSERT+SELECT only — scripts/setup_db_roles.sql)
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
