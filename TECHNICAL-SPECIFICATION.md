<!-- ARCHIVED 2026-07-06: superseded by implementation — see README for current state -->

# Jeli Technical Specification: Tiered Storage + Security Architecture

**Version:** 1.0-draft  
**Date:** 2026-06-06  
**Status:** In Development (Phase 1 PoC)  
**Related:** CLAUDE.md, README.md, Curation Algorithm, Deployment Plan

---

## Overview

This specification combines Jeli's **security/governance framework** (from existing CLAUDE.md) with **tiered storage architecture** (from investigation findings) to create a scalable, performant, trustworthy personal memory system.

**Core insight:** Jeli's three-branch governance ensures *trust*; tiering ensures *relevance*. Together: trustable AND relevant memory that scales.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    CAPTURE LAYER                                 │
│  Browser ext · App hooks · CLI · Voice · Clipboard · Git · Conv │
└────────────────────────────────┬────────────────────────────────┘
                                 │ raw events
┌────────────────────────────────▼────────────────────────────────┐
│                    INGESTION LAYER                               │
│  Dedup · Classify · Extract entities · Embeddings · Provenance  │
│  Hash-chain writes → append log (immutable)                      │
└────────────────────────────────┬────────────────────────────────┘
                                 │ structured memories (JSON + vectors)
┌────────────────────────────────▼────────────────────────────────┐
│                    TIERED STORAGE LAYER                          │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │   L0: Hot    │  │  L1: Primary │  │ L2: Warm   │ L3: Cold  │
│  │   (RAM)      │  │  (PG Hot)    │  │ (PG Warm)  │ (Archive) │
│  │  <1ms        │  │  5-50ms      │  │ 100-500ms  │ 1-10s    │
│  │  ~1MB        │  │  ~100MB      │  │ ~1GB+      │ Unlimited│
│  │  Pointers    │  │  Curated +   │  │ Historical │ Deleted  │
│  │  Hot facts   │  │  Recent (30d)│  │ Context    │ Archive  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │       │
│       │                 │                   │            │       │
│       └─────────────────┬───────────────────┴────────────┘       │
│                         │ pointers (L0→L1, L1→L2, L2→L3)         │
│                         │                                        │
│       ┌─────────────────▼───────────────────────────────────┐   │
│       │    HASH-CHAIN APPEND LOG (Immutable Audit Trail)    │   │
│       │  Blake3 hashes · Ed25519 signatures · Timestamps    │   │
│       │  All mutations timestamped, never destructive       │   │
│       └─────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────┐
│                  INTELLIGENCE LAYER                              │
│  • Curation Engine (importance scoring, decay, access patterns)  │
│  • Contradiction Detection (poisoning defense)                   │
│  • Semantic Search (pgvector + BM25)                             │
│  • Temporal Invalidation (facts age, never delete)               │
│  • Conflict Resolution (Judicial branch)                         │
└────────────────────────────────┬────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────┐
│              SCOPED MCP / REST API (Jeli Layer)                 │
│  • Access Control (read/write by agent, scope, policy)           │
│  • Injection Detection (contradiction scoring on writes)         │
│  • Integrity Verification (hash-chain validation)                │
│  • Constitutional Enforcement (user veto layer)                  │
└──────────────────────────────────────────────────────────────────┘
                                 │
                ┌────────────────┴────────────────┐
                ▼                                  ▼
        ┌─────────────────┐            ┌──────────────────┐
        │  Agents         │            │  Users           │
        │  Hermes/Claude  │            │  (Constitutional) │
        └─────────────────┘            └──────────────────┘
```

---

## Storage Schema

### Layer 0: Hot Memory (In-Process Cache + Redis Local)

**Purpose:** Immediate context, last few interactions, pointers to deeper layers

**Size:** 100KB - 1MB  
**Latency:** <1ms  
**TTL:** 1 hour (LRU eviction) or manual pin  
**Data model:** Key-value object (JSON)

```python
# Pseudo-schema
class HotMemory:
    # In-memory + Redis local
    last_conversations: List[ConversationSnapshot]  # last 3-5
    current_context: Dict[str, Any]  # user goal, recent facts
    frequently_accessed: List[MemoryPointer]  # L1 IDs, accessed in last hour
    created_at: Timestamp
    ttl_expires_at: Timestamp  # auto-evict to L1
```

**Operations:**
- `get(key)` → O(1) lookup
- `set(key, value)` → O(1) write + TTL
- `evict_lru()` → move oldest to L1, free space
- `promote(L1_id)` → fetch from L1, pin to L0

---

### Layer 1: Primary (PostgreSQL Hot Partition)

**Purpose:** Curated, important, recent facts — what matters THIS MONTH

**Size:** 10-100MB  
**Latency:** 5-50ms (indexed, warm cache)  
**Indexes:** Full-text (tsvector), vector (pgvector), importance score  
**Data model:** PostgreSQL table

```sql
CREATE TABLE memories_l1_primary (
    id UUID PRIMARY KEY,
    -- Core record
    kind TEXT NOT NULL,  -- semantic|episodic|procedural|factual|relational
    body JSONB NOT NULL,
    
    -- Immutability + Provenance
    created_at TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ,
    valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_to TIMESTAMPTZ,  -- NULL = still valid
    
    -- Governance
    supersedes UUID[],  -- array of IDs this replaces
    superseded_by UUID[],  -- populated by engine on revise
    
    -- Security + Trust
    content_hash BYTEA NOT NULL,  -- Blake3
    signature BYTEA,  -- Ed25519 over content_hash
    trust_score NUMERIC(3,2),  -- 0.0 to 1.0
    source TEXT,  -- "user_direct"|"agent_inferred"|"external"
    
    -- Curation + Ranking
    importance SMALLINT DEFAULT 3,  -- 0-10, user-curated
    salience NUMERIC(3,2),  -- 0.0-1.0, decay over time
    access_count BIGINT DEFAULT 0,
    last_accessed TIMESTAMPTZ,
    
    -- Scope + Policy
    scope JSONB,  -- {owner, project, agent, visibility}
    consent JSONB,  -- {retention, exportable, redact: []}
    
    -- Metadata
    metadata JSONB,  -- type, topics, people, action_items, dates
    embedding VECTOR(1024),  -- snowflake-arctic-embed2
    embedding_model TEXT,
    embedding_timestamp TIMESTAMPTZ,
    
    -- Indexing
    created_at_idx TIMESTAMP,  -- for time-range queries
    topic_tags TEXT[],  -- extracted from metadata
    
    CONSTRAINT valid_temporal CHECK (valid_from <= COALESCE(valid_to, NOW())),
    CONSTRAINT trust_score_range CHECK (trust_score >= 0.0 AND trust_score <= 1.0)
);

-- Indexes
CREATE INDEX idx_memories_l1_created ON memories_l1_primary (created_at DESC);
CREATE INDEX idx_memories_l1_importance ON memories_l1_primary (importance DESC, access_count DESC);
CREATE INDEX idx_memories_l1_tsvector ON memories_l1_primary USING GIN (to_tsvector('simple', body::text));
CREATE INDEX idx_memories_l1_embedding ON memories_l1_primary USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX idx_memories_l1_supersedes ON memories_l1_primary USING GIN (supersedes);
CREATE INDEX idx_memories_l1_scope ON memories_l1_primary USING GIN (scope);

-- Partitioning by creation month (for efficiency)
CREATE TABLE memories_l1_primary_2026_06 PARTITION OF memories_l1_primary
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
```

**Operations:**
- `recall(query, scope, limit)` → search tsvector + vector, rank by importance
- `remember(memory_data)` → validate hash, check for contradictions, write + hash-chain
- `revise(id, new_data)` → create new record, link via supersedes
- `forget(id, reason)` → tombstone (mark valid_to), reason in metadata
- `get(id)` → fetch by ID + verify signature
- `evict_to_l2()` → move facts older than 30 days, not frequently accessed, not important

---

### Layer 2: Warm (PostgreSQL Warm Partition or Separate Table)

**Purpose:** Historical context, episodic memories OLDER THAN 30 DAYS

**Size:** 1GB+  
**Latency:** 100-500ms  
**Indexes:** Time-based (created_at), topic/person (metadata)  
**Data model:** Same schema as L1, different table/partition

```sql
-- Similar to L1, but different indexes (minimal vector)
CREATE TABLE memories_l2_warm (
    -- Same columns as memories_l1_primary
    -- BUT different indexes:
    -- - Time-range queries only
    -- - Full-text indexed (metadata-only)
    -- - NO vector index (too expensive for 1GB+)
);

-- Partitioning by YEAR (for manageability)
CREATE TABLE memories_l2_warm_2026 PARTITION OF memories_l2_warm
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
```

**Operations:**
- `escalate_search(query, start_year)` → search L2 by time + full-text
- `restore_to_l1(id)` → promote fact from L2 to L1 (user accessed it)
- `evict_to_l3(cutoff_date)` → archive to L3 on schedule

---

### Layer 3: Cold (Object Store + File Archive)

**Purpose:** Deleted/tombstoned records, long-term archive, BLOBS (documents, images)

**Size:** Unlimited  
**Latency:** 1-10 seconds  
**Storage:** Files, object store (S3-compatible or local disk)  
**Data model:** Serialized records (JSON) + pointer manifest

```python
# L3 Structure (files on disk)
/jeli-archive/
├── manifests/
│   ├── 2026.json          # pointer index for year 2026
│   └── 2025.json
├── records/
│   ├── 2026/
│   │   ├── 01/            # month
│   │   │   ├── record-uuid-1.json
│   │   │   ├── record-uuid-2.json
│   └── blobs/
│       ├── doc-uuid-1.pdf
│       ├── img-uuid-2.jpg

# Manifest structure
{
    "year": 2026,
    "total_records": 50000,
    "date_range": ["2026-01-01", "2026-12-31"],
    "records": [
        {
            "id": "uuid-1",
            "created_at": "2026-06-04T10:00:00Z",
            "content_hash": "blake3:...",
            "status": "tombstoned|archived",
            "reason": "user_deleted|expired|archived"
        }
    ]
}
```

**Operations:**
- `archive_record(id, metadata)` → serialize, write to L3, update manifest
- `retrieve_from_archive(id, year)` → fetch from L3, optionally restore to L2
- `export_records(date_range, format)` → generate portable export from L3

---

## Hash-Chain & Immutability

All writes to L1/L2/L3 are appended to an **immutable audit log** (append-only, never overwrite).

```sql
CREATE TABLE memory_audit_log (
    sequence_id BIGSERIAL PRIMARY KEY,  -- incremental, never reused
    memory_id UUID NOT NULL,
    operation TEXT NOT NULL,  -- create|revise|forget|evict|restore
    
    -- Hash chain
    previous_hash BYTEA,  -- Blake3 of previous entry
    content_hash BYTEA NOT NULL,  -- Blake3 of this operation
    signature BYTEA NOT NULL,  -- Ed25519 signature over content_hash
    
    -- Provenance
    actor_did TEXT NOT NULL,  -- DID of who/what wrote this
    actor_kind TEXT,  -- user|agent|system
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB,  -- operation-specific data
    
    -- Validation
    CONSTRAINT hash_chain_integrity CHECK (previous_hash IS NOT NULL OR sequence_id = 1)
);

-- Only index for sequential reads (don't need random access)
CREATE INDEX idx_audit_log_memory ON memory_audit_log (memory_id, sequence_id DESC);
```

**Property:** `jeli verify` walks this log, recomputes all hashes, detects breaks immediately.

---

## Retrieval Algorithm

### Simple (Fast Path)

```
recall(query, context, limit=10):
  1. Check L0 (does hot memory have answer?)
     - If confidence > 0.9 → return with score
  2. If not, search L1 (curated layer)
     - Full-text + vector search
     - Rank by: semantic similarity + importance + recency
     - If confidence > 0.7 → return top-K
  3. If not confident, escalate to L2
     - Time-bounded search (last year only)
     - Return if found
  4. If user explicitly asks old question → search L3 (manifest query)
```

### Intelligent (Better Results)

```
recall(query, context, strategy="smart"):
  1. Classify query intent:
     - "What happened today?" → L0/L1
     - "Did I ever..." → L1/L2
     - "Archive search?" → L3
  2. Search estimated layer + adjacent
  3. Rank by: semantic (vector) + importance (user) + temporal (decay)
  4. Prefetch: if user accesses L2 fact → promote to L1 (it matters)
  5. Return ranked results + confidence scores
```

### Curation Feedback Loop

```
After recall, if user interacts with result:
  - Accessed? → increment access_count, boost importance
  - Ignored? → no change
  - Contradicted? → flag for Judicial review
  - Marked important? → bump importance score, keep in L1
```

---

## Curation System (Automatic + Manual)

### Automatic Curation (Engine-Driven)

**Importance Scoring:**
```python
importance_score = (
    base_importance * 0.4  # user-set (0-10)
    + (access_count / max_access_count) * 0.3  # frequency
    + (is_recent ? 0.3 : 0.0)  # recency (last 30 days)
    + (contradiction_count * -0.05)  # penalize contradictions
)
```

**Salience Decay (Half-Life):**
```python
salience = base_salience * (0.5 ^ (days_since_creation / 90))
# Facts decay to half-salience every 90 days
```

**Access Pattern Tracking:**
```python
# Track what user/agents access
# Use to predict what to prefetch/promote
if fact_accessed_in_session:
    increment(fact.access_count)
    update(fact.last_accessed)
    if fact.access_count > threshold:
        promote_from_l2_to_l1()
```

### Manual Curation (User-Driven)

```python
# User commands
jeli mark-important fact-id  # keeps in L1 forever (unless unmarked)
jeli archive fact-id         # move to L3 immediately
jeli delete fact-id reason   # tombstone + reason
jeli restore fact-id         # restore from L2/L3 to L1
```

---

## PostgreSQL Configuration

**For local Mac Mini (16GB RAM, external SSD):**

```sql
-- postgresql.conf tuning for single-user local system
shared_buffers = 4GB             # 25% of RAM
effective_cache_size = 12GB      # 75% of RAM
work_mem = 100MB                 # per operation
maintenance_work_mem = 1GB       # for vacuum/reindex
random_page_cost = 1.1           # SSD penalty
effective_io_concurrency = 200   # SSD parallel I/O
```

**Partitioning:**
- L1 hot partition: monthly (2026-06, 2026-07, etc.)
- L2 warm partition: yearly (2026, 2025, etc.)
- L3 archive: yearly files (2026/, 2025/)

---

## Scoped MCP Interface

Agents (Hermes, Claude) access Jeli via **scoped MCP tools** (not direct DB):

```python
# Available to agents:
jeli.remember(memory_data, scope)
  → Validates trust score
  → Checks for contradictions
  → Writes to L1 + audit log
  → Returns: {id, created_at, confidence}

jeli.recall(query, scope, filters)
  → Searches L0/L1
  → Ranks by importance
  → Returns: [{id, text, trust_score, source}, ...]

jeli.get(id, scope)
  → Fetches by ID
  → Verifies signature
  → Returns: {id, text, provenance, created_at}

jeli.feedback(memory_id, interaction)
  → Reports if memory was followed|ignored|contradicted
  → Updates salience/importance
  → Surfaces contradictions to Judicial

# NOT exposed to agents:
jeli.revise()    # User only
jeli.forget()    # User only (or Judicial)
jeli.promote()   # Engine only
jeli.evict()     # Engine only
```

---

## Deployment Stack

**Local Mac Mini:**
- PostgreSQL 17 (local, port 5442)
- Redis (optional, for L0 cache)
- Python 3.11+ (Alembic migrations, curation engine)
- Node.js/Deno (MCP server, API endpoints)
- launchd (Mac-native startup)

**Optional OB1 Integration:**
- OB1 Ingestion Layer (capture, dedup, classify)
- Jeli Storage Layer (trust, governance, tiering)
- Shared PostgreSQL or federated

---

## Success Criteria (Phase 1 PoC)

- [ ] L0 + L1 tables created, migrations working
- [ ] Hash-chain audit log implemented
- [ ] Simple recall (L0/L1 only) working
- [ ] Eviction policy (L1 → L2) functional
- [ ] MCP tools expose recall/remember safely
- [ ] 10k test memories, search latency <100ms for L1
- [ ] Deployed on JP's Mac Mini, running stable 7 days

---

## Next Phases

**Phase 2:** Curation engine (importance scoring, decay curves, prefetching)  
**Phase 3:** Contradiction detection (memory poisoning defense)  
**Phase 4:** L2/L3 tiering, archive operations  
**Phase 5:** OB1 integration (optional)

