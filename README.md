# Jeli

> A seamless, sovereign, cryptographically-attested personal memory system that captures everything, forgets nothing useful, surfaces insights automatically, and plugs into any agent — requiring zero extra effort from the user.

## The Problem

Major platforms (Apple, Microsoft, Google, OpenAI, Anthropic) are converging on the same play: **capture your behavior, preferences, and context inside their walled garden**, build a model of you, and use it to serve you better — on their terms.

Once your memory lives in Apple Intelligence or Copilot, you cannot leave without losing years of accumulated context. The vendor controls the format, API, deletion policy, and can change all of these unilaterally.

## The Solution

Jeli is a personal memory framework where:

- **You own the data** — full schema access, full export, no proprietary format lock-in
- **Architecture makes exfiltration detectable** — cryptographic provenance traces every memory to its origin
- **No single vendor required** — any component (storage, inference, agents) can be swapped
- **Independence is structural** — sovereignty enforced by the system, not promised by terms of service
- **Open standard** — others can adopt it; not forced into vendor memory systems

## Architecture

Jeli is built on **three-branch governance** (separation of powers):

```
Executive (Agents) ←→ Legislative (Memory Store) ←→ Judicial (Conflict Resolution)
                              ↓
                    Constitutional Layer (User-Signed, Inviolable)
```

- **Executive:** Hermes, Claude, future agents — propose memories, cannot write directly
- **Legislative:** PostgreSQL + pgvector (OB1/lf2b) — canonical source of truth with append-only audit log
- **Judicial:** Conflict resolution engine — arbitrates contradictions using trust scores, precedent, provenance
- **Constitutional:** User-signed, cryptographically inviolable rules — data stays local, no PII leaves machine, user veto on irreversible actions

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

**Current:** Designing the Scoped MCP server (access policy model, caller identity, permission schema, interface contract).

**Next:** Prototype hash-chain append log on OB1, implement Judicial conflict resolution engine, build consolidation/dreaming loop.

## Documentation

- **Full Vision & Technical Decisions:** See `Library/AI/projects/legionforge-memory-framework-vision.md` (in Obsidian vault)
- **Architecture & Development:** See `CLAUDE.md` in this repository
- **Security Posture & Data Integrity:** See `Library/AI/memory/lessons/lessons-security.md` and `lessons-data.md` (in Obsidian vault)

## License

MIT License — see `LICENSE` file.

Copyright (c) 2026 JP Cruz (jp@legionforge.org)

---

**Jeli is a public good.** The LegionForge memory framework exists so individuals and organizations are not forced into vendor-controlled memory systems. Build with sovereignty in mind.
