# Changelog

## Unreleased

### Added
- Hash-chained state events (alembic 006): `jeli revise` / `jeli invalidate`
  — user-tier, never MCP tools; temporal columns become a verified cache;
  `jeli verify` walks both chains and cross-checks. Closes the
  THREAT-MODEL temporal-fields gap.
- `jeli_user` DB role: column-level UPDATE grants (temporal fields only) —
  content structurally unwritable even for the user tier
- Markdown import pipeline (`scripts/import_markdown.py`) + search
  benchmark (`scripts/bench_search.py`); asymmetric query prefixes
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
