# Jeli

> A security and governance layer for personal memory systems. Cryptographically verifiable. Poison-proof. Sovereign.

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

Jeli is built on **three-branch governance** (separation of powers):

```
Executive (Agents) ←→ Scoped MCP ←→ Legislative (Memory Store) ←→ Judicial (Conflict Resolution)
                      (Jeli Layer)                           ↓
                                          Constitutional Layer (User-Signed, Inviolable)
```

- **Executive:** Hermes, Claude, future agents — propose memories via MCP only
- **Scoped MCP (Jeli's Access Control):** Enforces security policy, validates integrity, detects injection
- **Legislative:** PostgreSQL + pgvector (optional: OB1) — canonical source with append-only hash chain
- **Judicial:** Conflict resolution engine — arbitrates contradictions using trust scores, precedent, provenance
- **Constitutional:** User-signed, cryptographically inviolable layer — data stays local, no PII leaves machine, user veto on irreversible actions

## 4-Layer System Stack

```
Capture Layer        → raw events (browser, apps, CLI, voice, clipboard, git, conversations)
Ingestion Layer      → dedup, classify, extract entities, embeddings, provenance, hash-chain
Storage Layer        → Postgres + pgvector, KV store, graph, audit log, object store
Intelligence Layer   → dreaming, consolidation, contradiction detection, insight surfacing
Agent API (MCP)      → unified interface for any agent to read/write memories
```

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

**Proposed Partnership:**
- **OB1** handles ingestion, retrieval, multi-AI access (what it does best)
- **Jeli** adds security, governance, cryptographic guarantees (what it does best)
- **Together:** Trustworthy, sovereign memory that multiple AIs can safely use

**Installation Model:** Jeli is optional and installable:
- Install: `jeli init --with-ob1` (adds security tables, Scoped MCP)
- Use: OB1 works as-is; Jeli layer is opt-in
- Remove: `jeli uninstall --keep-ob1` (drops Jeli tables, OB1 unaffected)

Users who don't need security use OB1 standalone. Users who do get cryptographic guarantees.

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

- **Storage:** PostgreSQL + pgvector (existing OB1/lf2b)
- **Agent Interface:** MCP (Model Context Protocol)
- **Agents:** Hermes (primary), Claude/Dispatch, future agents
- **Embedding Model:** Ollama local (qwen3-embedding) or cloud (OpenAI) — tradeoff: sovereignty vs quality
- **Backup & DR:** Local + encrypted remote (S3-compatible or peer)

## Status

**Current:** 
- ✅ Full implementation plan complete (Scoped MCP Server)
- ✅ Partnership proposal submitted to OB1 (awaiting feedback)
- 🚧 Ready to begin Phase 1 implementation

**Current (Phase 1, shipped on this branch):** Scoped MCP server (stdio) with `capture_memory` / `search_memory` / `audit_trail` / `verify_chain`, hash-chained writes with per-record signing-key identity, injection defense with trust capping, and the `jeli verify` CLI.

**Next:**
- pgvector migration + semantic search mode
- Contradiction detection on the write path (Phase 3)
- Integrate with OB1 (if partnership approved) OR deploy standalone
- Implement Judicial conflict resolution engine
- Build consolidation/dreaming loop

## Quick Start

```bash
pip install -e ".[dev]"
pytest                       # 127 tests, no services required
alembic upgrade head         # requires PostgreSQL

export SCOPED_MCP_API_KEY=...        # generate: python -c 'import secrets; print(secrets.token_urlsafe(32))'
export SCOPED_MCP_CHAIN_KEY=...      # HMAC key for the hash chain — guard like a root credential
python -m jeli_scoped_mcp            # stdio MCP server
jeli verify                          # walk the chain, report first tampered record
```

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `SCOPED_MCP_DB_URL` | `postgresql://...:5433/openbrain` | PostgreSQL connection |
| `SCOPED_MCP_API_KEY` | *(required)* | server auth key |
| `SCOPED_MCP_CHAIN_KEY` | *(required)* | HMAC signing key for the hash chain |
| `SCOPED_MCP_CHAIN_KEY_ID` | `k1` | identity of the active chain key (rotation: new key ⇒ new id; old records verify under their own key) |
| `SCOPED_MCP_AGENT_ACTOR` | `unknown-agent` | principal stamped on every write/audit row — set per agent instance; not settable by the agent itself |
| `SCOPED_MCP_EMBEDDING_PROVIDER` | `openai` | `openai` or `ollama` |
| `SCOPED_MCP_TRANSPORT` | `stdio` | MCP transport |

## Contributing / Repo hygiene

This repo ships a pre-push scrub hook that scans every commit being pushed for internal identifiers. Enable it once per clone:

```bash
git config core.hooksPath .githooks
```

## Documentation

- **Architecture & Development:** See `CLAUDE.md` in this repository
- **Extended Documentation:** Project vision, security posture, and data integrity guidelines live in your vault

## License

MIT License — see `LICENSE` file.

Copyright (c) 2026 JP Cruz (jp@legionforge.org)

---

**Jeli is a public good.** The LegionForge memory framework exists so individuals and organizations are not forced into vendor-controlled memory systems. Build with sovereignty in mind.
