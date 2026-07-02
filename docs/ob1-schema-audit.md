# OB1 Schema Audit — Jeli Storage Adapter Reference

**Date:** 2026-05-20  
**Purpose:** Document the existing OB1/lf2b Postgres schema so the Jeli Storage Adapter knows exactly what it needs to wrap when OB1 is used as the first storage backend.  
**Scope:** Read-only audit — no changes made to any schema or data.

---

## 1. OB1 Core `thoughts` Table

The `thoughts` table is the central storage primitive in OB1. Its exact shape depends on which setup path was used, but the canonical Supabase/main deployment is the target for the Storage Adapter.

### 1a. Base columns (from `docs/01-getting-started.md` + `recipes/vercel-neon-telegram/sql/001-create-thoughts.sql`)

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` | Supabase variant; k8s self-hosted uses `BIGSERIAL` |
| `content` | `TEXT` | NOT NULL | Raw thought text |
| `embedding` | `vector(1536)` | nullable | pgvector; HNSW index (cosine ops); **1536 dimensions** |
| `metadata` | `JSONB` | `DEFAULT '{}'` | GIN-indexed; houses `type`, `topics[]`, `people[]`, `source`, `action_items[]`, etc. |
| `created_at` | `TIMESTAMPTZ` | `DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ` | `DEFAULT now()` | maintained by `thoughts_updated_at` trigger |

> **Embedding dimensions: 1536** — matches OpenAI `text-embedding-3-small`. Confirmed in both the Supabase and k8s schema files, and in Jeli's own `memory_entry` design.

The Vercel-Neon variant also carries:

| Column | Type | Notes |
|--------|------|-------|
| `source` | `TEXT NOT NULL DEFAULT 'mcp'` | Capture origin |

### 1b. Dedup column (getting-started Step 2.6)

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `content_fingerprint` | `TEXT` | `UNIQUE WHERE content_fingerprint IS NOT NULL` | Computed by the upsert function; used to merge metadata on duplicate captures |

### 1c. Enhanced-thoughts columns (`schemas/enhanced-thoughts/schema.sql`)

All added via `ALTER TABLE thoughts ADD COLUMN IF NOT EXISTS` — idempotent, may or may not be present on a given installation.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `type` | `TEXT` | null | Backfilled from `metadata->>'type'`; vocab: idea, task, person_note, reference, decision, lesson, meeting, journal |
| `sensitivity_tier` | `TEXT` | `'standard'` | `'standard'` or `'restricted'` |
| `importance` | `SMALLINT` | `3` | 1–10 scale |
| `quality_score` | `NUMERIC(5,2)` | `50` | 0–100 quality signal |
| `source_type` | `TEXT` | null | Backfilled from `metadata->>'source'` |
| `enriched` | `BOOLEAN` | `false` | Set to true once entity extraction completes |

### 1d. Indexes on `thoughts`

| Index name | Type | Columns | Condition |
|------------|------|---------|-----------|
| `thoughts_embedding_idx` | HNSW | `embedding vector_cosine_ops` | — |
| `thoughts_metadata_idx` | GIN | `metadata` | — |
| `thoughts_created_at_idx` | btree | `created_at DESC` | — |
| `idx_thoughts_content_tsvector` | GIN | `to_tsvector('simple', content)` | — |
| `idx_thoughts_type` | btree | `type` | — |
| `idx_thoughts_importance` | btree | `importance DESC` | — |
| `idx_thoughts_source_type` | btree | `source_type` | — |
| `idx_content_fingerprint` | btree | `content_fingerprint` | `WHERE content_fingerprint IS NOT NULL` |

### 1e. RPCs on `thoughts`

| Function | Purpose |
|----------|---------|
| `match_thoughts(query_embedding, match_threshold, match_count, filter)` | Vector cosine similarity search |
| `upsert_thought(p_content, p_payload)` | Insert-or-merge by content_fingerprint |
| `search_thoughts_text(p_query, p_limit, p_filter, p_offset)` | Full-text search with tsvector + ILIKE fallback |
| `brain_stats_aggregate(p_since_days, p_exclude_restricted)` | JSONB aggregate: total count + top types + top topics |
| `get_thought_connections(p_thought_id, p_limit, p_exclude_restricted)` | Thoughts sharing metadata topics/people with a given thought |

---

## 2. Additional OB1 Tables (schemas/ and recipes/)

These tables are optional add-ons and may or may not be present in any given lf2b deployment.

### 2a. `entities` (`schemas/entity-extraction/schema.sql`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGSERIAL` | PK |
| `entity_type` | `TEXT NOT NULL` | person, project, topic, tool, organization, place |
| `canonical_name` | `TEXT NOT NULL` | |
| `normalized_name` | `TEXT NOT NULL` | lowercase/trimmed; UNIQUE(entity_type, normalized_name) |
| `aliases` | `JSONB DEFAULT '[]'` | |
| `metadata` | `JSONB DEFAULT '{}'` | |
| `first_seen_at` | `TIMESTAMPTZ` | |
| `last_seen_at` | `TIMESTAMPTZ` | |
| `created_at` | `TIMESTAMPTZ` | |
| `updated_at` | `TIMESTAMPTZ` | |

### 2b. `edges` (entity-to-entity) (`schemas/entity-extraction/schema.sql` + `schemas/typed-reasoning-edges/schema.sql`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGSERIAL` | PK |
| `from_entity_id` | `BIGINT` | FK entities.id ON DELETE CASCADE |
| `to_entity_id` | `BIGINT` | FK entities.id ON DELETE CASCADE |
| `relation` | `TEXT NOT NULL` | co_occurs_with, works_on, uses, related_to, member_of, located_in |
| `support_count` | `INT DEFAULT 1` | |
| `confidence` | `NUMERIC(3,2)` | |
| `metadata` | `JSONB DEFAULT '{}'` | |
| `created_at` | `TIMESTAMPTZ` | |
| `updated_at` | `TIMESTAMPTZ` | |
| `valid_from` | `TIMESTAMPTZ` | Added by typed-reasoning-edges migration |
| `valid_until` | `TIMESTAMPTZ` | Added by typed-reasoning-edges migration |
| `decay_weight` | `NUMERIC(3,2)` | Added by typed-reasoning-edges migration |

UNIQUE (from_entity_id, to_entity_id, relation)

### 2c. `thought_entities` (join table)

| Column | Type | Notes |
|--------|------|-------|
| `thought_id` | `UUID` | FK thoughts.id ON DELETE CASCADE |
| `entity_id` | `BIGINT` | FK entities.id ON DELETE CASCADE |
| `mention_role` | `TEXT DEFAULT 'mentioned'` | |
| `confidence` | `NUMERIC(3,2)` | |
| `source` | `TEXT DEFAULT 'entity_worker'` | |
| `evidence` | `JSONB DEFAULT '{}'` | |
| `created_at` | `TIMESTAMPTZ` | |
| `updated_at` | `TIMESTAMPTZ` | |

UNIQUE (thought_id, entity_id, mention_role)

### 2d. `entity_extraction_queue`

| Column | Type | Notes |
|--------|------|-------|
| `thought_id` | `UUID` | PK; FK thoughts.id ON DELETE CASCADE |
| `status` | `TEXT DEFAULT 'pending'` | pending → processing → complete/failed/skipped |
| `attempt_count` | `INT DEFAULT 0` | |
| `last_error` | `TEXT` | |
| `queued_at` | `TIMESTAMPTZ` | |
| `started_at` | `TIMESTAMPTZ` | |
| `processed_at` | `TIMESTAMPTZ` | |
| `source_fingerprint` | `TEXT` | Snapshot of content_fingerprint at queue time |
| `source_updated_at` | `TIMESTAMPTZ` | |
| `worker_version` | `TEXT` | |
| `metadata` | `JSONB DEFAULT '{}'` | |

Trigger `trg_queue_entity_extraction` fires on INSERT or UPDATE of `content`/`metadata` on thoughts, skipping rows with `metadata->>'generated_by'` set.

### 2e. `consolidation_log`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGSERIAL` | PK |
| `operation` | `TEXT NOT NULL` | dedup_merge, metadata_fix, bio_synthesis, etc. |
| `survivor_id` | `UUID` | |
| `loser_id` | `UUID` | |
| `details` | `JSONB DEFAULT '{}'` | |
| `created_at` | `TIMESTAMPTZ` | |

### 2f. `thought_edges` (thought-to-thought) (`schemas/typed-reasoning-edges/schema.sql`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGSERIAL` | PK |
| `from_thought_id` | `UUID` | FK thoughts.id ON DELETE CASCADE |
| `to_thought_id` | `UUID` | FK thoughts.id ON DELETE CASCADE |
| `relation` | `TEXT NOT NULL` | CHECK IN (supports, contradicts, evolved_into, supersedes, depends_on, related_to) |
| `confidence` | `NUMERIC(3,2)` | 0.0–1.0 |
| `decay_weight` | `NUMERIC(3,2)` | 0.0–1.0 |
| `valid_from` | `TIMESTAMPTZ` | |
| `valid_until` | `TIMESTAMPTZ` | NULL = still current |
| `classifier_version` | `TEXT` | |
| `support_count` | `INT DEFAULT 1` | |
| `metadata` | `JSONB DEFAULT '{}'` | |
| `created_at` | `TIMESTAMPTZ` | |
| `updated_at` | `TIMESTAMPTZ` | trigger-maintained |

UNIQUE (from_thought_id, to_thought_id, relation); CHECK (from_thought_id <> to_thought_id)

### 2g. `graph_nodes` + `graph_edges` (`recipes/ob-graph/schema.sql`)

`graph_nodes`: UUID id, user_id UUID, label TEXT, node_type TEXT DEFAULT 'entity', properties JSONB, thought_id UUID (optional FK), timestamps.  
`graph_edges`: UUID id, user_id UUID, source_node_id/target_node_id FK, relationship_type TEXT, weight REAL DEFAULT 1.0, properties JSONB, created_at.

---

## 3. Jeli `memory_entry` Schema (for reference)

Source: `alembic/versions/001_initial_jeli_schema.py` + `src/jeli_scoped_mcp/database/migrations.py`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK, DEFAULT gen_random_uuid() | |
| `content` | `TEXT` | NOT NULL | |
| `content_hash` | `VARCHAR(64)` | NOT NULL | SHA-256 of content |
| `embedding` | `vector(1536)` | NOT NULL | IVFFlat cosine index (WHERE valid_until IS NULL) |
| `embedding_model` | `TEXT/VARCHAR(255)` | NOT NULL | e.g. `'openai/text-embedding-3-small'` |
| `embedding_dimensions` | `INT` | NOT NULL | 1536 |
| `embedded_at` | `TIMESTAMPTZ` | NOT NULL | When embedding was generated |
| `metadata` | `JSONB` | NOT NULL, DEFAULT '{}' | |
| `trust_score` | `NUMERIC(3,2)` | NOT NULL | 0.3 (external) – 1.0 (user-stated) |
| `memory_type` | `VARCHAR(50)` | NOT NULL | preference, identity, episodic, etc. |
| `prev_hash` | `VARCHAR(256)` | nullable | Hash-chain: previous record's HMAC-SHA256 |
| `record_hash` | `VARCHAR(256)` | NOT NULL, UNIQUE | HMAC-SHA256(chain_key, canonical_json(record)) |
| `valid_from` | `TIMESTAMPTZ` | DEFAULT now() | |
| `valid_until` | `TIMESTAMPTZ` | nullable | NULL = still active |
| `superseded_by` | `UUID` | FK memory_entry.id ON DELETE SET NULL | |
| `amended_from` | `UUID` | FK memory_entry.id ON DELETE SET NULL | |
| `delta_embedding` | `vector(1536)` | nullable | Embedding diff for amendment audit |
| `created_at` | `TIMESTAMPTZ` | DEFAULT now() | |
| `created_by` | `VARCHAR(255)` | NOT NULL | Discord user ID or agent name |
| `session_id` | `UUID` | nullable | |
| `source_agent` | `VARCHAR(100)` | nullable | hermes, claude, dispatch *(alembic migration only; not yet in migrations.py)* |
| `provenance_ref` | `UUID` | FK memory_entry.id ON DELETE SET NULL | |

Supporting tables: `memory_audit_log` (append-only, BIGSERIAL PK, FK memory_entry), `memory_contradiction` (UUID PK, FK pairs to memory_entry, resolved/severity/judicial_ruling_id).

---

## 4. Gap Analysis — What OB1 Is Missing for Jeli

The following Jeli `memory_entry` columns have no direct equivalent in OB1's `thoughts` table:

| Jeli column | Status in OB1 | Risk / note |
|-------------|--------------|-------------|
| `content_hash` | `content_fingerprint` exists but is dedup-only, not a cryptographic hash; different semantics | Partial — would need a separate SHA-256 column |
| `embedding_model` | **Missing** | OB1 stores a fixed 1536-dim embedding but never records which model produced it |
| `embedding_dimensions` | **Missing** | Implicit in the vector column definition, never a stored value |
| `embedded_at` | **Missing** | No timestamp for when the embedding was generated |
| `trust_score` | **Missing** — closest approximates are `importance` (SMALLINT 1-10) and `quality_score` (NUMERIC 0-100), but neither carries the same provenance semantics | Mapping is lossy; a formula like `trust_score ≈ importance/10` loses origin intent |
| `prev_hash` | **Missing** | Hash-chain integrity field; no equivalent in OB1 |
| `record_hash` | **Missing** | HMAC-SHA256 tamper-evidence field; no equivalent in OB1 |
| `valid_from` | **Missing on thoughts** (present on thought_edges and entity edges only) | |
| `valid_until` | **Missing on thoughts** | |
| `superseded_by` | **Missing** | OB1 has no record version chain |
| `amended_from` | **Missing** | |
| `delta_embedding` | **Missing** | Amendment audit vector diff |
| `created_by` | **Missing** — OB1 is single-user, no actor tracking | |
| `session_id` | **Missing** | |
| `source_agent` | `source_type` (enhanced-thoughts) and `metadata->>'source'` are related but store source system/type, not agent identity | Partial |
| `provenance_ref` | **Missing** | |

**Approximate mappings (close but not exact):**

| OB1 field | Jeli field | Delta |
|-----------|-----------|-------|
| `type` | `memory_type` | Same concept, different controlled vocabularies |
| `quality_score` + `importance` | `trust_score` | OB1 splits quality (LLM-rated) from importance (user-rated); Jeli merges into one provenance-aware score |
| `source_type` | `source_agent` | OB1 tracks source system; Jeli tracks named agent |
| `content_fingerprint` | `content_hash` | Both dedup keys; OB1 fingerprint is not a standard SHA-256 |

**Fields OB1 has that Jeli does not use:**

| OB1 field | Note |
|-----------|------|
| `sensitivity_tier` | Useful for scoping MCP tool access; no Jeli equivalent (could map to metadata) |
| `enriched` | Entity-extraction status flag; Jeli handles extraction state via audit log |
| `updated_at` | OB1 updates in-place; Jeli uses `valid_from`/`valid_until` for temporal tracking |

---

## 5. Migration Notes — Storage Adapter Design Options

### Option A — Extend `thoughts` with Jeli columns (ALTER TABLE)

Add all missing Jeli fields directly to the `thoughts` table via non-destructive `ADD COLUMN IF NOT EXISTS`.

Columns to add: `content_hash TEXT`, `embedding_model TEXT`, `embedding_dimensions INT`, `embedded_at TIMESTAMPTZ`, `trust_score NUMERIC(3,2)`, `prev_hash TEXT`, `record_hash TEXT UNIQUE`, `valid_from TIMESTAMPTZ`, `valid_until TIMESTAMPTZ`, `superseded_by UUID`, `amended_from UUID`, `delta_embedding vector(1536)`, `created_by TEXT`, `session_id UUID`, `source_agent TEXT`, `provenance_ref UUID`.

- **Pro:** Single table; OB1's REST API and all existing RPCs work without modification.
- **Con:** Pollutes OB1's schema with Jeli-specific fields; any OB1 upgrade that touches the thoughts table becomes a coordination point; other OB1 users sharing the instance see Jeli columns.

### Option B — Sidecar table `jeli_meta` (1:1 FK to `thoughts`)

Create a separate `jeli_meta` table holding only the Jeli-specific fields, joined to `thoughts` via `thought_id UUID PK FK thoughts.id`.

```sql
CREATE TABLE jeli_meta (
  thought_id        UUID PRIMARY KEY REFERENCES thoughts(id) ON DELETE CASCADE,
  content_hash      TEXT NOT NULL,
  embedding_model   TEXT NOT NULL,
  embedding_dimensions INT NOT NULL,
  embedded_at       TIMESTAMPTZ NOT NULL,
  trust_score       NUMERIC(3,2) NOT NULL,
  prev_hash         TEXT,
  record_hash       TEXT NOT NULL UNIQUE,
  valid_from        TIMESTAMPTZ DEFAULT now(),
  valid_until       TIMESTAMPTZ,
  superseded_by     UUID REFERENCES thoughts(id) ON DELETE SET NULL,
  amended_from      UUID REFERENCES thoughts(id) ON DELETE SET NULL,
  created_by        TEXT NOT NULL,
  session_id        UUID,
  source_agent      TEXT,
  provenance_ref    UUID REFERENCES thoughts(id) ON DELETE SET NULL
);
```

- **Pro:** Zero risk to OB1 schema; OB1-native tooling unaffected; clean extension point.
- **Con:** Every Jeli read requires a JOIN; writes must be atomic across two tables (use a transaction).

### Option C — Jeli-native `memory_entry` + OB1 as vector search backend (recommended for Phase 1)

Keep Jeli's own `memory_entry` table exactly as designed. Use OB1 only for semantic search: the Storage Adapter syncs content+embedding into `thoughts` and queries `match_thoughts()` for vector recall. Canonical storage (hash-chain, trust, provenance) stays entirely in `memory_entry`.

- **Pro:** Cleanest separation of concerns; Jeli's hash-chain and tamper-evidence semantics are never at risk; OB1 can be swapped out for any other pgvector backend later.
- **Con:** Content and embedding are duplicated between `memory_entry` and `thoughts`; OB1's MCP tools (`search_thoughts`, `list_thoughts`) only see raw content without Jeli metadata.

**Recommended migration path:**
1. Phase 1 — implement Option C. Jeli writes to `memory_entry`, syncs embeddings into OB1 `thoughts` for search.
2. Phase 2 — if deep OB1 integration is needed (e.g. reuse OB1's entity extraction or thought_edges), migrate to Option B: add the `jeli_meta` sidecar and wire the Storage Adapter to hydrate it.

---

## 6. Embedding Details Summary

| System | Dimension | Index type | Metric | Model tracked? |
|--------|-----------|------------|--------|----------------|
| OB1 `thoughts.embedding` | 1536 | HNSW | cosine | No — hardcoded in edge function |
| OB1 k8s `thoughts.embedding` | 1536 | (no HNSW; only btree on metadata) | cosine | No |
| Jeli `memory_entry.embedding` | 1536 | IVFFlat | cosine | Yes — `embedding_model` + `embedding_dimensions` columns |
| Jeli `memory_entry.delta_embedding` | 1536 | none | n/a | Amendment diff only |

Both systems default to 1536 dimensions. Changing embedding model would require a full re-embed and schema migration in OB1 (no model column to gate on); Jeli handles this via `embedding_model` + `embedding_dimensions` stored per-row.

---

*Note: The task specified `/Volumes/MAC_MINI_1TB/jeli/docs/` but that path does not exist. This file was written to `/Volumes/MAC_MINI_1TB/LegionForge-jeli/docs/` which is the actual jeli repository location.*
