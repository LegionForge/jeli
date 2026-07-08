# Jeli - an optional security add on for Nate B. Jones' [Open Brain](https://github.com/NateBJones-Projects/OB1)

> A security and governance layer for personal memory systems. Cryptographically verifiable. Poison-resistant. Sovereign.

## Why "Jeli"?

A [jeli](https://en.wikipedia.org/wiki/Griot) (ߖߋ߬ߟߌ, the northern Mande name for a griot) is a West African oral historian — the living memory of a community. For centuries, jelis have carried genealogies, histories, and agreements across generations, trusted precisely because the role carries accountability: a jeli's word is verifiable against the community's collective memory, and a jeli serves as **mediator** when accounts conflict.

That is exactly what this project does for personal AI memory: keep the record faithfully (hash-chained provenance), attest where every fact came from (trust-scored writes), mediate when memories contradict (the judicial layer), and answer to the person whose memory it keeps — never to the systems writing into it (the constitutional layer). The name is a commitment: memory as a trust, not a commodity.

The name is also personal. [*The Singing Man*](https://www.goodreads.com/en/book/show/3067748) by Angela Shelf Medearis, illustrated by Terea Shaffer (Holiday House, 1994), retells the Yoruba story of Banzar — the son who chooses music over an approved trade, apprentices himself to an old praise singer, and returns home carrying his people's songs and histories. I read that book to my son Dylan from the time he was a baby until he was a young boy, and its lessons stayed with our family: that the keepers of songs and histories are keepers of identity — mediators and advisors, entrusted with what a community knows about itself — and that following your passion can itself become a gift to the world. Medearis, Shaffer, and the praise singers they honor have had a profound impact on me and my family. This project is my attempt to live up to that: to build something as useful to the people who rely on it as a praise singer is to a village.

## The Problem

As of 2026, memory poisoning attacks are documented and active:
- **MINJA attack (arXiv 2025):** 95%+ injection success, 70%+ attack success
- **Microsoft Security (Feb 2026):** "AI Recommendation Poisoning" in dozens of companies
- **Palo Alto Unit 42:** Indirect prompt injection through documents poisoning long-term memory

Plus the vendor lock-in problem: Once your memory lives in Apple Intelligence or Copilot, you cannot leave without losing years of accumulated context.

## The Solution

Jeli adds cryptographic integrity and governance to memory systems:

- **Hash-chained memories** — detect silent corruption or tampering
- **Contradiction detection** — flag poisoned or conflicting facts
- **Full provenance** — every memory traces to its origin with audit trail
- **Trust scoring** — distinguish user-stated (1.0) from agent-inferred (0.6) from external (0.3)
- **Temporal boundaries** — facts age and invalidate; old records never delete
- **Amendment tracking** — full history of how facts changed
- **User veto** — you control irreversible agent actions
- **Structural sovereignty** — security enforced by architecture, not promises

## Architecture

Jeli is built on **three-branch governance** — separation of powers between the agents that propose memories, the store that holds them, and the engine that resolves contradictions. A cryptographically inviolable Constitutional layer sits beneath all three.

> The diagram below is the conceptual model. For the code-level view — which module implements which branch, and the write/read/verify paths step by step — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), or take the [guided CodeTour](#guided-code-walkthrough-vs-code) in VS Code.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#0d1117', 'mainBkg': '#161b22', 'primaryColor': '#1c2938', 'primaryBorderColor': '#30363d', 'primaryTextColor': '#e6edf3', 'lineColor': '#6e7681', 'clusterBkg': '#161b22', 'clusterBorder': '#30363d', 'edgeLabelBackground': '#161b22', 'titleColor': '#e6edf3'}}}%%

flowchart TB
    classDef agent      fill:#0d2137,stroke:#4a90d9,stroke-width:2px,color:#a8d4ff
    classDef jeliGreen  fill:#0d1f15,stroke:#3fb950,stroke-width:2px,color:#7ee787
    classDef jeliTeal   fill:#0a1e1e,stroke:#39c5cf,stroke-width:2px,color:#79e8ef
    classDef storage    fill:#1f0d2a,stroke:#a371f7,stroke-width:2px,color:#d2a8ff
    classDef judicial   fill:#1f1808,stroke:#e3b341,stroke-width:2px,color:#f0c842
    classDef daemon     fill:#1f160d,stroke:#f0883e,stroke-width:2px,color:#ffa657
    classDef constStyle fill:#0d0d18,stroke:#484f58,stroke-width:2px,stroke-dasharray:6 4,color:#8b949e

    subgraph EXEC["Executive — Agents"]
        direction LR
        H(["Hermes"]):::agent
        CL(["Claude / Codex"]):::agent
        FA(["Future Agents"]):::agent
    end

    subgraph JELI["Jeli — Security and Governance Layer"]
        direction TB

        subgraph MCP["Scoped MCP Server"]
            direction LR
            AUTH["Auth · HMAC · rate-limit"]:::jeliGreen
            IDEF["Injection defense · trust cap"]:::jeliGreen
            TOOLS["capture_memory · search_memory · audit_trail · summarize_session (user tier: jeli verify · revise · invalidate · redact)"]:::jeliGreen
        end

        subgraph BOUNCER["Ingestion Bouncer"]
            direction LR
            INBOX["memory_inbox staging"]:::jeliTeal
            CLF["IngestionClassifier · dedup · classify"]:::jeliTeal
            WRK["InboxWorker x N  FOR UPDATE SKIP LOCKED"]:::jeliTeal
            INBOX --> CLF --> WRK
        end

        MCP -->|"approved writes"| BOUNCER
    end

    subgraph LEGIS["Legislative — Storage"]
        direction LR
        PG[("PostgreSQL 16 + pgvector")]:::storage
        CHAIN["memory_entry · HMAC-SHA256 hash-chained · vector1024 HNSW"]:::storage
        AUDIT["memory_audit_log · memory_state_event · conflict_queue"]:::storage
    end

    subgraph DAEMONS["Background Daemons"]
        direction LR
        CRD["ConflictResolverDaemon · trust-score arbitration"]:::judicial
        INS["InsightsDaemon · consolidation · decay"]:::daemon
        MNT["MaintenanceDaemon · chain compaction"]:::daemon
    end

    CONST[/"Constitutional Layer  ·  User-signed  ·  Cryptographically Inviolable  ·  Data stays local  ·  No PII off-machine  ·  User veto on irreversible actions"/]:::constStyle

    EXEC  -->|"stdio / HTTP · MCP calls"| MCP
    WRK   -->|"hash-chained append"| PG
    MCP   -->|"search / audit / verify"| PG
    PG    -->|"pg_notify INSERT trigger"| CRD
    CRD   -->|"precedent log"| PG
    CRD   -.->|"unresolvable conflict"| CONST
    INS   <-->|"read + annotate"| PG
    MNT   <-->|"compact + prune"| PG
    CONST -.->|"inviolable constraints"| MCP

    style EXEC    fill:#0d1525,stroke:#4a90d9,stroke-width:2px,color:#e6edf3
    style JELI    fill:#0a130d,stroke:#3fb950,stroke-width:2px,color:#e6edf3
    style MCP     fill:#0d1f15,stroke:#3fb950,stroke-width:1px,color:#7ee787
    style BOUNCER fill:#0a1e1e,stroke:#39c5cf,stroke-width:1px,color:#79e8ef
    style LEGIS   fill:#150d22,stroke:#a371f7,stroke-width:2px,color:#e6edf3
    style DAEMONS fill:#1a1208,stroke:#e3b341,stroke-width:2px,color:#e6edf3
```

**Branches:**
- **Executive (Agents):** Hermes, Claude, Codex — propose memories via MCP only; no direct DB access
- **Scoped MCP:** Jeli's enforcement point — authenticates callers, caps trust on flagged content, logs every operation
- **Ingestion Bouncer:** Staging layer before hash-chain commit — dedup, classify, entity extraction, N-instance safe queue
- **Legislative (Storage):** PostgreSQL + pgvector — append-only hash-chained log; no silent UPDATE or DELETE
- **Judicial (Daemons):** ConflictResolverDaemon arbitrates contradictions; InsightsDaemon runs consolidation; unresolvable conflicts surface to user
- **Constitutional:** Cryptographically signed constraints no branch can override — data stays local, user veto on irreversible actions

## Cryptographic Integrity

- Every memory write is **hash-chained** — no silent overwrites
- **Full provenance** — every fact traces to its origin with cryptographic attestation
- **Temporal invalidation** — facts never delete, only invalidate with temporal boundaries
- **Embedding provenance** — every vector stores its model, dimensions, and embedding timestamp
- **Trust-scored writes** — user-direct (1.0) vs agent-inferred (0.6) vs external (0.3)

Defense against active memory poisoning attacks (MINJA, Microsoft, Palo Alto documented 2026).

## Threat Model & Defense

As of 2026, memory poisoning attacks on AI agents are actively documented:

- **MINJA attack (arXiv 2025):** 95%+ injection success, 70%+ attack success under realistic conditions
- **Microsoft Security (Feb 2026):** "AI Recommendation Poisoning" — dozens of companies exploiting at scale
- **Palo Alto Unit 42:** Indirect prompt injection through documents that poison personal agent memory persistently

**Jeli's defense:** Cryptographic integrity layer makes injected memories detectable — they either break the hash chain or carry low/foreign trust score that the judicial layer flags and surfaces.

Verification command: `jeli verify` — walks the provenance log, recomputes all hashes, flags breaks.

## OB1 Integration

Jeli is designed to work **alongside** [OB1](https://github.com/NateBJones-Projects/OB1) (by Nate B. Jones), a personal memory system that excels at multi-source ingestion and semantic search.

The diagram below shows the integration points — where each system's responsibility begins and ends, and how they share a PostgreSQL cluster without interfering with each other.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#0d1117', 'mainBkg': '#161b22', 'primaryColor': '#1c2938', 'primaryBorderColor': '#30363d', 'primaryTextColor': '#e6edf3', 'lineColor': '#6e7681', 'clusterBkg': '#161b22', 'clusterBorder': '#30363d', 'edgeLabelBackground': '#161b22', 'titleColor': '#e6edf3'}}}%%

flowchart TB
    classDef agent      fill:#0d2137,stroke:#4a90d9,stroke-width:2px,color:#a8d4ff
    classDef jeliNode   fill:#0d1f15,stroke:#3fb950,stroke-width:2px,color:#7ee787
    classDef jeliTeal   fill:#0a1e1e,stroke:#39c5cf,stroke-width:2px,color:#79e8ef
    classDef ob1Node    fill:#16112b,stroke:#a371f7,stroke-width:2px,color:#d2a8ff
    classDef dbJeli     fill:#1a0d2a,stroke:#a371f7,stroke-width:2px,color:#d2a8ff
    classDef dbOb1      fill:#0f1629,stroke:#58a6ff,stroke-width:2px,color:#79c0ff
    classDef judicial   fill:#1f1808,stroke:#e3b341,stroke-width:2px,color:#f0c842
    classDef daemon     fill:#1f160d,stroke:#f0883e,stroke-width:2px,color:#ffa657
    classDef constStyle fill:#0d0d18,stroke:#484f58,stroke-width:2px,stroke-dasharray:6 4,color:#8b949e
    classDef future     fill:#0d1117,stroke:#484f58,stroke-width:1px,stroke-dasharray:4 4,color:#6e7681

    subgraph AGENTS["Agents"]
        direction LR
        H(["Hermes"]):::agent
        CL(["Claude"]):::agent
        CD(["Codex"]):::agent
        OTH(["Other MCP Clients"]):::agent
    end

    subgraph JELI_GATE["Jeli — Write Gateway"]
        direction TB
        SMCP["Scoped MCP Server · Auth · HMAC · injection defense · agent trust ceiling · capture_memory · summarize_session · search_memory · audit_trail"]:::jeliNode
        BNCR["Ingestion Bouncer · memory_inbox staging · IngestionClassifier · InboxWorker x N parallel"]:::jeliTeal
        SMCP -->|"approved"| BNCR
    end

    subgraph OB1_GATE["OB1 — Read Gateway  port 8100"]
        OB1MCP["OB1 MCP Server · Multi-source ingestion · Semantic search + retrieval · Multi-AI access · Conversational memory API"]:::ob1Node
    end

    subgraph SHARED_DB["Shared PostgreSQL Cluster"]
        direction LR

        subgraph JELI_SCHEMA["Jeli Schema  non-destructive add"]
            JT[("memory_entry hash-chained · memory_inbox staging · memory_audit_log · memory_state_event · conflict_queue")]:::dbJeli
        end

        subgraph OB1_SCHEMA["OB1 Schema  untouched by Jeli"]
            OT[("ob1_memories · ob1_embeddings · ob1_conversations · ob1_sources")]:::dbOb1
        end
    end

    subgraph JELI_DAEMONS["Jeli Background Daemons"]
        direction LR
        CRD["ConflictResolverDaemon · Judicial arbitration · precedent log"]:::judicial
        INS["InsightsDaemon · consolidation · decay"]:::daemon
    end

    CONST[/"Constitutional Layer  ·  User-signed  ·  Cryptographically Inviolable  ·  Data stays local  ·  No PII off-machine  ·  User veto on irreversible actions"/]:::constStyle

    FUTURE[/"Future: OB1 writes route through Jeli Bouncer for full provenance coverage"/]:::future

    AGENTS     -->|"write · audit · verify"| JELI_GATE
    AGENTS     -->|"search · recall"| OB1_GATE
    BNCR       -->|"hash-chained append"| JT
    OB1MCP    <-->|"reads + ingests"| OT
    JT         -.->|"pg_notify conflict trigger"| CRD
    CRD & INS <-->|"operates on"| JT
    CRD        -.->|"unresolvable conflict"| CONST
    CONST      -.->|"inviolable bounds"| SMCP
    OT         -.->|"integrity layer"| FUTURE
    FUTURE     -.->|"routes through"| BNCR

    style AGENTS       fill:#0d1525,stroke:#4a90d9,stroke-width:2px,color:#e6edf3
    style JELI_GATE    fill:#0a130d,stroke:#3fb950,stroke-width:2px,color:#e6edf3
    style OB1_GATE     fill:#110d1e,stroke:#a371f7,stroke-width:2px,color:#e6edf3
    style SHARED_DB    fill:#0f0f1a,stroke:#484f58,stroke-width:2px,color:#e6edf3
    style JELI_SCHEMA  fill:#150d22,stroke:#a371f7,stroke-width:1px,color:#d2a8ff
    style OB1_SCHEMA   fill:#0d1525,stroke:#58a6ff,stroke-width:1px,color:#79c0ff
    style JELI_DAEMONS fill:#1a1208,stroke:#e3b341,stroke-width:2px,color:#e6edf3
```

**Integration points:**

| Point | What happens |
|---|---|
| **Write path** | Agents call Jeli's Scoped MCP → Bouncer → hash-chained `memory_entry`. Jeli enforces integrity before any data lands. |
| **Read path** | Agents call OB1's MCP server directly. OB1 handles retrieval; Jeli doesn't duplicate it. |
| **Shared PostgreSQL cluster** | Jeli creates its own tables (`jeli init --with-ob1`) alongside OB1's schema. No OB1 table is touched. |
| **Future: write wrapping** | OB1 writes can route through Jeli's Bouncer so all memories — regardless of source — carry hash-chain provenance. (Dotted line above.) |
| **Removal** | `jeli uninstall --keep-ob1` drops Jeli tables only. OB1 is unaffected. |

**Division of responsibility:**
- **OB1** — ingestion breadth, retrieval quality, multi-AI access (what it does best)
- **Jeli** — cryptographic integrity, injection defense, trust scoring, audit trail, user veto (what it does best)
- **Together** — trustworthy, sovereign memory that multiple AIs can safely use, with a verifiable chain of custody for every fact

**Current Status:** Exploring partnership with Nate. See [Extension Proposal](https://github.com/NateBJones-Projects/OB1/issues) for feedback. Can also be deployed standalone.

---

## First Build: Scoped MCP Server

The **Scoped MCP is the access control layer** between agents (Hermes, Claude) and the memory vault. It solves the blast-radius problem:

Without it, agents have unrestricted filesystem read/write and shell access. With the Scoped MCP, agents can only call explicitly-defined tools:

- `capture_memory` — write to append-only log (user-confirmed or low-trust agent inferences)
- `search_memory` — query interface (semantic, FTS, SQL, graph traversal)
- `summarize_session` — trigger consolidation/dreaming
- `audit_trail` — read provenance chain

No shell, no arbitrary file access, all calls logged with source (agent ID, session, timestamp).

## Memory Types

| Type | Description | Decay | Examples |
|------|-------------|-------|----------|
| **Preference** | How you like things done | Very slow | "prefers directness", "no bullet lists" |
| **Identity** | Who you are, roles, goals | Near-zero | Skills, values, projects, context |
| **Episodic** | What happened, when | Medium | Session summaries, decisions made |
| **Semantic** | Domain knowledge, facts | Slow | Architecture patterns, lessons learned |
| **Procedural** | How to do things | Slow | Workflows, recipes, playbooks |
| **Transient** | In-flight working memory | Fast | Current task state, open threads |

## Technology Stack

- **Storage:** PostgreSQL + pgvector
- **Agent Interface:** MCP (Model Context Protocol)
- **Agents:** Hermes (primary), Claude/Dispatch, future agents
- **Embedding Model:** Ollama local (snowflake-arctic-embed2 default) or OpenAI (cloud opt-in) — sovereignty vs quality tradeoff
- **Backup & DR:** Local + encrypted remote (S3-compatible or peer)

### Why HNSW at 1024 dimensions

The vector index is `vector(1024)` with an HNSW (Hierarchical Navigable Small World) index. This is an increasingly common choice across the vector database ecosystem and worth explaining.

**What HNSW is.** HNSW builds a multi-layer graph over your vectors. Each layer is a "small world" graph where any two nodes are reachable in a logarithmic number of hops. Search starts at the top (coarse) layer and progressively narrows toward the nearest neighbors at the bottom layer. The result: sub-millisecond approximate nearest-neighbor (ANN) queries even at tens of thousands of records, with recall rates typically above 95%.

**Why the ecosystem is converging on it.** HNSW has two properties that IVFFlat (the older alternative) lacks: it needs no training phase — you can add records one at a time without rebuilding — and it maintains high recall across a wide range of dataset sizes without tuning. This makes it the practical default for systems where the dataset grows continuously and reindexing is expensive. pgvector added HNSW in v0.5.0 (2023) specifically because it outperforms IVFFlat for most real workloads. Qdrant, Weaviate, ChromaDB (via hnswlib), and Redis all use HNSW as their primary index. Elasticsearch added it in 8.0. It is the approximate nearest-neighbor algorithm that most major vector databases have settled on.

**Why 1024 dimensions specifically.** Several high-quality embedding models converge naturally on 1024 dims: `snowflake-arctic-embed2` and `snowflake-arctic-embed` emit 1024 natively; `qwen3-embedding` and `BAAI/bge-m3` also produce 1024-dim vectors. OpenAI's `text-embedding-3-small` supports matryoshka representation learning (MRL) and can be truncated to exactly 1024 with negligible quality loss. This makes 1024 the highest-quality dimension count that is interoperable across local (Ollama), cloud (OpenAI), and multilingual (Qwen3, bge-m3) providers without a schema migration when you switch models.

At personal memory scale (1k–100k records) an HNSW index at 1024 dims fits comfortably in RAM, queries in under a millisecond, and never needs to be retrained. **Changing embedding models in Jeli is a re-embedding job, never a schema migration** — the 1024-dim index stays the same regardless of which model produced the vectors.

## Status

**Current (v0.2.0-alpha):** the full three-branch governance model is implemented and tested — Scoped MCP server (`capture_memory` / `search_memory` / `audit_trail` / `search_by_entity` / `get_entity_graph`), HMAC hash-chained writes with per-record signing-key identity, layered injection defense (regex + unicode normalization + opt-in LLM classifier), Constitutional Read/Write gates over user-signed rules, Judicial conflict resolution with precedent case law and human escalation, entity graph auto-extraction, memory portability (export/import with tamper detection), and the Ingestion Bouncer. 482 unit tests (82% coverage) + 17 live-Postgres integration tests. Index standard: `vector(1024)` — arctic-embed2 native, Qwen3-Embedding MRL ceiling, OpenAI truncatable; model swaps are re-embedding jobs, never schema migrations.

Deployed in production on local hardware since v0.1.0-alpha (2026-07-02).

**Next:**
- OB1/lf2b integration (partnership exploration ongoing; standalone deployment works today)
- Capture breadth decision (browser extension vs app hooks vs clipboard)
- Default-on lightweight heuristic for natural-language injection rephrasing (GH #33 remainder)

## Quick Start

```bash
pip install -e ".[dev]"
pytest                       # 482 unit tests, no services required
alembic upgrade head         # requires PostgreSQL

export SCOPED_MCP_API_KEY=...        # generate: python -c 'import secrets; print(secrets.token_urlsafe(32))'
export SCOPED_MCP_CHAIN_KEY=...      # HMAC key for the hash chain — guard like a root credential
python -m jeli_scoped_mcp            # stdio MCP server
jeli verify                          # walk the chain, report first tampered record
```

Live integration tests (disposable pgvector container, auto-torn-down):

```bash
bash scripts/run_integration_tests.sh          # port 5433 by default
JELI_TEST_DB_PORT=5599 bash scripts/run_integration_tests.sh   # if 5433 is taken
```

### New in v0.2.0-alpha

The three-branch governance model and the poisoning defenses are now usable from the CLI:

1. **Constitutional layer** — user-signed, hash-chained constraints that no agent can override. `jeli constitutional list` shows active rules; `jeli constitutional add` signs a new one (e.g. cap external content at trust 0.3, or deny agent writes of a memory type):
   ```bash
   jeli constitutional add --rule-type max_trust_for_content_class \
     --parameters '{"content_class":"external","max_trust":0.3}' \
     --description "External content capped at 0.3 trust"
   ```

2. **Judicial precedent** — settled contradictions become case law. `jeli judicial precedents` lists resolved conflicts; `jeli judicial pending` shows conflicts escalated for human review; `jeli judicial resolve --entry-id <id> --resolution <ruling>` resolves one.

3. **Entity graph** — every `capture_memory` now auto-extracts entities (people, projects, organizations, technologies). `jeli graph entities` lists known entities; `jeli graph search --entity "JP Cruz"` finds every memory mentioning someone; `jeli graph relations --entity "Jeli"` shows an entity's relations and linked-memory count.

4. **Memory portability** — `jeli export > backup.jsonl` streams your store (metadata, no raw vectors) to a sovereignty-preserving JSON-Lines archive; `jeli import backup.jsonl` re-imports it with SHA-256 tamper detection and provenance stamping (re-embedding locally, chaining into a fresh chain).

5. **Operational** — `jeli verify --report` produces a full integrity health report (chain + state-chain validity, cache consistency, trust/queue stats); `jeli re-embed` re-embeds stale records after an embedding-model change; `jeli decay-report` lists memories whose effective trust has decayed significantly from their stored score.

> The optional LLM injection classifier (Layer 2 of the injection defense) ships behind the `[llm]` extra: `pip install -e ".[llm]"`. It fails open and only screens sources below trust 0.8. See [SECURITY.md](SECURITY.md) §5.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `SCOPED_MCP_DB_URL` | `postgresql://jeli_app:...:5442/jeli` | PostgreSQL connection |
| `SCOPED_MCP_API_KEY` | *(required)* | server auth key |
| `SCOPED_MCP_CHAIN_KEY` | *(required)* | HMAC signing key for the hash chain |
| `SCOPED_MCP_CHAIN_KEY_ID` | `k1` | identity of the active chain key (rotation: new key ⇒ new id; old records verify under their own key) |
| `SCOPED_MCP_AGENT_ACTOR` | `unknown-agent` | principal stamped on every write/audit row — set per agent instance; not settable by the agent itself |
| `SCOPED_MCP_EMBEDDING_PROVIDER` | `ollama` | local-first; `openai` is the opt-in (truncated to 1024 dims) |
| `OLLAMA_MODEL` | `snowflake-arctic-embed2` | must emit 1024 dims (the index standard); `qwen3-embedding` also supported |
| `SCOPED_MCP_EMBEDDING_DIMENSIONS` | auto | only needed for Ollama models not in the built-in dims map |
| `SCOPED_MCP_TRANSPORT` | `stdio` | MCP transport |

## Contributing / Repo hygiene

This repo ships a pre-push scrub hook that scans every commit being pushed for internal identifiers. Enable it once per clone:

```bash
git config core.hooksPath .githooks
```

## Documentation

| Doc | What it covers |
|---|---|
| [docs/background.md](docs/background.md) | **Why Jeli exists** — the driver (memory poisoning + memory enclosure), why governance instead of filters, the reasoning behind the 2026-07 hardening decisions, and where Jeli sits in the memory landscape |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | **Code-level architecture** — module map, the write/read/verify paths step by step, data model by migration, trust model, judicial case-law semantics |
| [SECURITY.md](SECURITY.md) | Threat model (MINJA, recommendation poisoning, IJPI) and every defense layer |
| [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md) | Extended threat analysis |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| `CLAUDE.md` | Development guidance for AI-assisted work in this repo |

### Guided code walkthrough (VS Code)

The repo ships three [CodeTour](https://marketplace.visualstudio.com/items?itemName=vsls-contrib.codetour) walkthroughs in [`.tours/`](.tours/). Install the CodeTour extension, open this repo, and the tours appear in the explorer sidebar:

1. **The Write Path** — follow one memory from an agent's MCP call through injection defense, the Constitutional WriteGate, and the advisory-locked hash-chain append
2. **Governance** — the Constitutional and Judicial branches: signed rules, read/write gates, precedent case law, human escalation
3. **Integrity & Verify** — canonical hashing, chained state events (never delete), `jeli verify`, and portable sovereignty

Tour steps anchor on code patterns rather than line numbers, so they survive refactors.

## Acknowledgements & Prior Art

Jeli was shaped by studying these projects and ideas. Direct attribution where their design influenced this codebase:

### Nate B. Jones — [OB1 / OpenBrain1](https://github.com/NateBJones-Projects/OB1)
The **Bouncer** pattern (memory inbox with pre-write classification) was directly inspired by Nate's talks and writing on OB1. His framing of confidence levels, importance/urgency tiers, and encoding resolution (raw vs. compressed vs. keyword) is the conceptual foundation of Jeli's `IngestionClassifier`. Jeli is designed to layer *on top of* OB1 as a security extension, not to replace it.

### Andrej Karpathy — [LLM OS / Memory Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
Karpathy's framing of LLMs as operating systems with distinct memory tiers (in-context, external KV, vector stores, database) directly informed Jeli's memory type taxonomy (Preference, Identity, Episodic, Semantic, Procedural, Transient) and the four-layer system stack design.

### mem0 / MemGPT / Letta
[mem0](https://github.com/mem-0/mem0) and [Letta](https://letta.com) (formerly MemGPT) demonstrated stateful agent memory APIs and the pattern of separating agent-loop from memory storage. Jeli's MCP interface design and trust-scored write model are informed by their work.

### Graphiti
[Graphiti](https://github.com/getzep/graphiti) demonstrated temporal graph memory for AI agents — the idea that facts have a `valid_from`/`valid_until` lifecycle rather than simple overwrite. This directly maps to Jeli's temporal invalidation model.

### Cognee
[Cognee](https://github.com/topoteretes/cognee) demonstrated a poly-store control plane (graph + vector + relational) as a memory orchestration layer. Cognee is a candidate integration for Jeli's Storage Layer.

---

## License

MIT License — see `LICENSE` file.

Copyright (c) 2026 JP Cruz (jp@legionforge.org)

---

**Jeli is a public good.** The LegionForge memory framework exists so individuals and organizations are not forced into vendor-controlled memory systems. Build with sovereignty in mind.
