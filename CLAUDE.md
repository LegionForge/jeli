# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project: Jeli — LegionForge Personal Memory Framework

**Repository:** https://github.com/LegionForge/jeli

**Vision:** A seamless, sovereign, cryptographically-attested personal memory system that captures everything, forgets nothing useful, surfaces insights automatically, and plugs into any agent — requiring zero extra effort from the user.

**Core Problem Being Solved:**
- Major platforms (Apple, Microsoft, Google, OpenAI, Anthropic) are converging on the same play: capture user behavior, preferences, context inside their walled gardens, and use it to serve you better **on their terms** — or worse, mine it for personalization at scale, sell as signals to advertisers, or share with insurers and lenders.
- Once your memory lives in Apple Intelligence or Copilot, you cannot leave without losing years of accumulated context. The vendor controls the format, API, deletion policy, and can change all of this unilaterally.

**The Jeli Alternative:**
- **User owns the data** — full schema access, full export, no proprietary format lock-in
- **Architecture makes exfiltration detectable** — cryptographic provenance traces every memory to its origin
- **No single vendor required** — any component (storage, inference, agents) can be swapped
- **Independence is structural** — sovereignty enforced by the system, not promised by ToS
- **Open standard** — others can adopt it; not forced into vendor memory systems

---

## Architecture Overview

### Three-Branch Governance (Separation of Powers)

Each branch has a distinct role; none can override its own constraints; all three check each other.

```
┌──────────────────────┐   proposes memories    ┌──────────────────────┐
│   EXECUTIVE          │ ─────────────────────▶ │   LEGISLATIVE        │
│   Agents             │                         │   Memory Store       │
│   Hermes · Claude    │ ◀───────────────────── │   OB1 / lf2b         │
│   future agents      │   reads prefs/facts/    │   Postgres+pgvector  │
│                      │   constraints           │                      │
└──────────┬───────────┘                         └──────────┬───────────┘
           │ hits contradiction                             │ conflict detected
           └─────────────────────┬──────────────────────────┘
                                 ▼
           ┌─────────────────────────────────────────────────────────┐
           │   JUDICIAL — Conflict Resolution Engine                 │
           │   Arbitrates conflicts · Sets precedent                 │
           │   Logs all rulings with reasoning (full audit trail)    │
           │   Unresolvable → surfaces to user (appellate process)   │
           └─────────────────────┬───────────────────────────────────┘
                                 │
                                 ▼
           ┌─────────────────────────────────────────────────────────┐
           │   CONSTITUTIONAL LAYER (User-only, cryptographically    │
           │   signed — inviolable by any branch)                    │
           │   Data sovereignty · No cloud without consent           │
           │   No PII off-machine · User veto on any agent action    │
           └─────────────────────────────────────────────────────────┘
```

**Executive (Agents):** Hermes, Claude, future agents. Propose memories; cannot write directly to canonical store; bound by judicial precedent.

**Legislative (Memory Store):** OB1/lf2b (Postgres + pgvector). Canonical source of preferences, facts, constraints. Append-only hash chain with full provenance. Three tiers: Constitutional → Statutes (slow-change preferences) → Case law (precedents).

**Judicial (Conflict Resolution):** Arbitrates contradictions using trust scores, recency, source authority, and precedent. All rulings logged with reasoning. User-appealable.

**Constitutional (User-Signed, Inviolable):** Data stays local. No PII leaves machine. User veto on irreversible actions. Cryptographically signed; tampering is detectable.

---

### Four-Layer System Stack

```
┌─────────────────────────────────────────────┐
│           CAPTURE LAYER                      │
│  Browser extension · App hooks · CLI · Voice │
│  Clipboard · Screenshot · Git · Conversation │
└──────────────────────┬──────────────────────┘
                       │ raw events
┌──────────────────────▼──────────────────────┐
│           INGESTION LAYER                    │
│  Dedup · Classify · Extract entities        │
│  Generate embeddings · Attach provenance    │
│  Hash-chain write to append log              │
└──────────────────────┬──────────────────────┘
                       │ structured memories
┌──────────────────────▼──────────────────────┐
│           STORAGE LAYER                      │
│  PostgreSQL + pgvector  (semantic search)   │
│  KV store (fast lookup by key/tag)          │
│  Graph (relationships, entity links)        │
│  Append-only audit log (hash chain)         │
│  Object store (blobs, screenshots, audio)   │
└──────────────────────┬──────────────────────┘
                       │
┌──────────────────────▼──────────────────────┐
│           INTELLIGENCE LAYER                 │
│  Dreaming / Consolidation (nightly cron)    │
│  Compaction · Contradiction detection       │
│  Insight surfacing · Trend analysis         │
│  Memory decay scoring                       │
└──────────────────────┬──────────────────────┘
                       │
┌──────────────────────▼──────────────────────┐
│           AGENT API (MCP)                   │
│  capture_memory · search · recall           │
│  summarize · update · redact · audit        │
│  Any agent plugs in via MCP protocol        │
└─────────────────────────────────────────────┘
```

---

## Memory Types & Data Model

| Type | Description | Decay | Examples |
|------|-------------|-------|----------|
| **Preference** | How JP likes things done | Very slow | "prefers directness", "no bullet lists" |
| **Identity** | Who JP is, roles, goals | Near-zero | Skills, values, projects, context |
| **Episodic** | What happened, when | Medium | Session summaries, decisions made |
| **Semantic** | Domain knowledge, facts | Slow | Architecture patterns, lessons learned |
| **Procedural** | How to do things | Slow | Workflows, recipes, playbooks |
| **Transient** | In-flight working memory | Fast | Current task state, open threads |

### Trust Model

All writes carry a trust level:

| Source | Trust | Notes |
|--------|-------|-------|
| User direct (typed/spoken) | 1.0 | Highest — ground truth |
| User confirmed (agent proposed, user approved) | 0.9 | Near-ground-truth |
| Agent inferred from conversation | 0.6 | Good signal, validate over time |
| Agent inferred from behavior/clicks | 0.4 | Noisy, needs corroboration |
| External source (web, docs) | 0.3 | Lowest, may be stale or wrong |

---

## Cryptographic Integrity & Provenance

**Core Principle:** Every memory write is hash-chained. No silent overwrites. Full audit trail with provenance.

**Memory Entry Schema (Minimal):**
```sql
memory_entry {
  id              uuid
  content         text
  embedding       vector          -- current embedding for retrieval
  metadata        jsonb           -- trust_score, source_agent, session_id, provenance_ref
  created_at      timestamp
  prev_hash       text            -- hash of previous record in this entity's chain
  record_hash     text            -- HMAC-SHA256(chain_key, canonical(content + metadata + prev_hash))
  amended_from    uuid?           -- if this amends an earlier record
  delta_embedding vector?         -- delta from prior embedding for integrity log
}
```

**Embedding Provenance (Non-Negotiable):**
Every embedding record must include:
```sql
embedding_model      TEXT        -- 'openai/text-embedding-3-small' or 'local/llm-valet:nomic-embed-text'
embedding_dimensions INT         -- 1536 (catches dimension mismatches early)
embedded_at          TIMESTAMPTZ -- enables time-based re-embedding queries
```

**Temporal Invalidation (Never Delete):**
Facts change, not delete. Old records preserved with temporal boundaries:
```sql
valid_from    TIMESTAMPTZ DEFAULT now()    -- when this version became true
valid_until   TIMESTAMPTZ                  -- NULL = currently valid
superseded_by UUID REFERENCES memories(id) -- points to replacement record
```

**Amendments Are Appends, Not Updates:**
No true UPDATE on memory records. A correction appends a new record with `amended_from: <original_id>`. Original hash preserved. Redactions zero content but keep hash and append a signed redaction event.

---

## Threat Model & Defenses

**Active Threat Landscape (as of 2026-05):**
- **MINJA attack (arXiv 2025):** Memory injection achieving 95%+ injection success, 70%+ attack success under realistic conditions
- **Microsoft Security (Feb 2026):** "AI Recommendation Poisoning" — dozens of companies exploiting at scale
- **Palo Alto Unit 42:** Indirect prompt injection through documents and web pages that poison personal agent long-term memory persistently
- **Decision drift:** A poisoned agent shifts behavior permanently without alarms

**Jeli's Defense:** Cryptographic integrity layer makes injected memories detectable — they either break the hash chain or carry low/foreign trust score that the judicial layer flags and surfaces.

**Jeli Verification Command:**
```bash
jeli verify  # Walks provenance log, recomputes all hashes, flags breaks, identifies first out-of-sync record
```

---

## Technology Stack & Component Decisions

### Storage Layer
- **Primary Store:** PostgreSQL + pgvector (existing OB1/lf2b)
  - Semantic search via vector similarity
  - Append-only audit log (hash-chained provenance)
  - KV lookups (fast tag/key retrieval)
  - Graph extensions (Apache AGE for entity relationships)
  - Object store (blobs, audio, screenshots)

### At Personal Scale (Embedded Stack)
Polyglot-lite pattern — use right tool for each job:
- **SQLite + FTS5 + sqlite-vec:** If no server process desired; single file; zero overhead
- **DuckDB:** OLAP/analytics layer; query Postgres directly without persistent overhead
- **Redis Streams (optional):** Episodic event ingestion buffer before Postgres write

**Avoid at personal scale:**
- Weaviate (35 GB RAM for 100k records)
- Pinecone (cloud-only = sovereignty violation)
- Neo4j (4–8 GB JVM overhead unless concrete multi-hop queries proven necessary)

### Agent Integration
- **Primary MCP Interface:** Extend OB1-MCP (port 8100) with new endpoints
  - `capture_memory`, `search` (semantic/FTS/SQL/graph), `recall`, `summarize`, `update`, `redact`, `audit`
- **Agents:** Hermes (primary), Claude/Dispatch, future agents
- **Embedding Model:** Ollama local (qwen3-embedding) vs cloud (OpenAI) — sovereignty vs quality tradeoff TBD

---

## Security & Integration Requirements

### Hermes Pre-Integration (Critical)

Hermes integration with Jeli carries **HIGH RISK** without mitigations. See `Library/AI/projects/hermes_vulnerability_assessment.md` for full threat model.

**Tier 1 Mitigations (Required Before Connecting):**

1. **Switch to Docker backend.** Set `terminal.backend: docker` in Hermes config. Single highest-impact mitigation.
   ```yaml
   # ~/.hermes/config.yaml
   terminal:
     backend: docker
   ```

2. **Strict Discord allowlist.** Allow only trusted user IDs.
   ```bash
   DISCORD_ALLOWED_USERS=<your_user_id>
   # Verify DISCORD_ALLOW_ALL_USERS is NOT in .env
   ```

3. **Dedicated, scoped Anthropic API key.** Do NOT reuse Claude Code credentials.
   ```bash
   # Create new key at console.anthropic.com for Hermes only
   # Ensure Docker container does not access ~/.claude/.credentials.json
   ```

4. **Filesystem write boundary.** Set `HERMES_WRITE_SAFE_ROOT` to non-vault directory.
   ```bash
   HERMES_WRITE_SAFE_ROOT=/Users/jp/hermes-workspace
   ```

5. **Harden file permissions immediately.**
   ```bash
   chmod 600 ~/.hermes/.env
   chmod 600 ~/.hermes/config.yaml
   chmod 600 ~/.hermes/memories/* 2>/dev/null || true
   chmod 700 ~/.hermes/logs/
   chmod 700 ~/.hermes/skills/
   ```

**Tier 2 Configuration Hardening:**
- Set `tirith_fail_open: false`
- Keep `approvals.mode: manual` (never `off`)
- Manually review `~/.hermes/skills/` after each external-input session
- Bind OB1/lf2b to `127.0.0.1:8100` (never `0.0.0.0`)
- Use read-only database user for Hermes's OB1 MCP connection

**Tier 3 Operational Practices:**
- Rotate Discord token and Anthropic API key quarterly
- Regularly audit `~/.hermes/skills/` for unexpected files
- Monitor `~/.hermes/logs/` for unexpected access patterns
- Install from pinned tag, not `main`: `uv pip install -e "." --pinned`

### Data Integrity Rules (Non-Negotiable)

From `Library/AI/memory/lessons/lessons-data.md`:

1. **Embedding provenance:** Every embedding triple (model, dimensions, embedded_at) stored alongside vector
2. **Never delete facts:** Temporal invalidation only (valid_until + superseded_by)
3. **Vault as source of truth:** Obsidian vault authoritative; Postgres is derived index with `source_path` pointing back
4. **Semantic search parity on re-embedding:** When embedding model changes, identify stale records and re-embed:
   ```sql
   SELECT id, content FROM memories
   WHERE embedding_model != 'target-model-id'
   ORDER BY embedded_at ASC;
   ```

### API Key Security (Lessons-Security)

**Comparison:** Always `hmac.compare_digest()`, never `==` or `!=` (timing oracle defense).
```python
import hmac
if not hmac.compare_digest(x_api_key, settings.api_key):
    raise HTTPException(status_code=401)
```

**Generation:** `secrets.token_urlsafe(32)` — 256 bits entropy, URL-safe alphabet.

**Storage:** `chmod 0600` on config files. Warn at startup if permissions too open.

**Transmission:** Use HTTP header (`X-API-Key`), never query parameter. Pre-SSL LAN cleartext is a known gap — document and mitigate with network-level controls (VLAN, firewall) until TLS.

---

## Development Workflow (TBD — Add as Code Arrives)

Once code structure is established, populate with:

- **Build:** How to build the Ingestion Layer, Storage Layer, Intelligence Layer, Agent API
- **Test:** Unit test suite structure, integration test against Postgres, MCP endpoint testing
- **Lint:** Code quality standards (ruff, Bandit, type checking)
- **Run Locally:** How to boot the full stack (Postgres + pgvector, OB1-MCP, Hermes, Obsidian sync)
- **Dreaming Loop:** How to invoke consolidation/contradiction detection scheduled task
- **Schema Migrations:** How to apply provenance schema extensions to existing OB1

---

## Key Open Questions (Refine Iteratively)

1. **Capture breadth:** Browser extension (keystrokes/clicks/navigation) vs app-level hooks vs clipboard only? Privacy boundary?
2. **Embedding model:** Ollama local (qwen3-embedding) vs cloud (OpenAI) — tradeoff: sovereignty vs quality?
3. **Blockchain anchoring:** Sigstore/Rekor (already in Firmwright) or none — cost vs value?
4. **Graph layer:** Add Neo4j/Apache Age alongside Postgres, or use Postgres graph extensions only?
5. **Dreaming schedule:** Nightly cron vs on-session-end vs both?
6. **Multi-user:** Is this JP-only or eventually multi-user (with per-user sovereignty boundaries)?
7. **Mobile capture:** Android for Hermes Discord — how does mobile interaction feed the system?
8. **DR target:** Where does the encrypted backup go? Local NAS, Backblaze B2, IPFS?

---

## Competitive Landscape & Build Strategy

### Platforms to Build On
- **Cognee (Apache 2.0):** Poly-store control plane for graph + vector + relational. Use it; don't rebuild.
- **Letta (Apache 2.0):** Stateful agent loop with memory API. Candidate for agent ↔ lf2b handoff.

### The Gap — What Nobody Has Built
| Missing Capability | Why It Matters |
|---|---|
| Append-only hash chain audit log | Detect unauthorized writes or deletions |
| Cryptographic provenance per memory | Every fact traces to a signed source |
| Judicial conflict resolution with precedent | Contradictions resolved consistently, not randomly |
| Constitutional layer (user-signed, tamper-evident) | Inviolable sovereignty constraints |
| Trust-scored writes | Agent-inferred ≠ user-stated ≠ external source |
| Cross-vendor memory portability standard | Move your graph between systems without data loss |
| Exfiltration detection | Know if your data left your machine |

**Jeli's contribution:** Build the sovereignty/governance stack on top of storage systems like Cognee. Open standard anyone can adopt.

---

## References & Canonical Docs

**In Obsidian Vault:**
- `Library/AI/projects/legionforge-memory-framework-vision.md` — Full vision, technical decisions, 4-layer architecture, integrity model, event sourcing design
- `Library/AI/memory/lessons/lessons-security.md` — Security posture, API key management, software evaluation discipline
- `Library/AI/memory/lessons/lessons-data.md` — Database, embedding, memory architecture lessons for storage adapter design
- `Library/AI/projects/hermes_vulnerability_assessment.md` — Why Hermes pre-integration security is critical; Scoped MCP is first build

**Public References:**
- [LegionForge GitHub](https://github.com/LegionForge)
- [Hindsight](https://hindsight.vectorize.io) — Local Postgres knowledge graph
- [Cognee](https://github.com/cognee) — Poly-store control plane (Apache 2.0)
- [Letta](https://letta.com) — Stateful agent memory (Apache 2.0)
- [Sigstore/Rekor](https://sigstore.dev) — Transparency log, already used in Firmwright
- [Firmwright PKI](docs/firmwright.md) — Hash-chaining + Sigstore pattern (in-house reference)

---

## First Build: Scoped MCP Server (Blocking Other Work)

Per `hermes_vulnerability_assessment.md`, the **Scoped MCP is the first thing to build** because:

1. **Blast radius problem:** Hermes has unrestricted filesystem read/write and shell access. Without a boundary, every system it connects to is at risk.
2. **MCP solves this structurally:** Define exactly which tools Hermes can call (e.g., memory search + capture, no shell, no arbitrary file access). The MCP server enforces the boundary.
3. **Required before Hermes integration:** Until the Scoped MCP exists, do not connect Hermes to the memory system.

**Scoped MCP deliverable:**
- Tool: `capture_memory(content, metadata, trust_score)` — write to append-only log
- Tool: `search_memory(query, mode=['semantic'|'fts'|'sql'], limit=10)` — query interface only, no write
- Tool: `summarize_session(content)` — trigger consolidation
- Tool: `audit_trail(memory_id)` — read provenance chain
- NO shell, NO arbitrary file read/write, NO unauthenticated access
- Requires MCP auth token from Hermes
- Logs all calls with source (Discord user ID, session, timestamp)

---

## Roadmap Sketch (From Vision)

- [ ] Decide on capture breadth (browser extension scope, app hooks)
- [ ] Evaluate Cognee for Storage Layer control plane
- [ ] Design provenance schema extension for OB1
- [ ] Prototype Scoped MCP server (FIRST PRIORITY)
- [ ] Prototype hash-chain append log on top of existing lf2b
- [ ] Design Hermes → OB1 write path (session end hook → MCP capture)
- [ ] Implement Judicial conflict resolution engine (trust scores + precedent)
- [ ] Consolidation/dreaming loop (nightly cron, contradiction detection)
- [ ] Redaction UX design (how does user redact memory from Discord?)
- [ ] Define DR strategy (backup target, shard strategy, restore procedure)

---

## Session-Specific Setup

When working on this repo, ensure:

1. **Obsidian vault is current** — check `Library/AI/memory/!startup.md` for active project state
2. **Hermes is sandboxed** — before any integration work, Tier 1 mitigations must be in place
3. **OB1/lf2b is available** — for testing, ensure Postgres + pgvector is running and MCP server at port 8100 is live
4. **No public commits without scrub** — check for internal IPs, SSH details, API keys before git push (global CLAUDE.md has the grep command)
