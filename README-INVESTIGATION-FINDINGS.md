<!-- ARCHIVED 2026-07-06: superseded by implementation — see README for current state -->

# Jeli: Investigation Findings & Implementation Roadmap

**Date:** 2026-06-06  
**Status:** Phase 1 PoC Ready  
**Created by:** Claude Code Investigation  
**Related:** See `/Volumes/MAC_MINI_1TB/universal-memory-protocol/jeli-research/` for full research

---

## Executive Summary

**Question:** Should LegionForge build Jeli, and how should it be architected?

**Answer:** **YES — build Jeli as a sovereign, trustable, tiered memory system.** The investigation identified a unique architectural approach combining:
1. **Jeli's existing security/governance framework** (from current CLAUDE.md)
2. **Tiered storage** (hot/warm/cool/cold) for scalability
3. **Automatic curation** (importance scoring, decay, frequency) for relevance

This combination addresses problems competitors don't solve.

---

## What Changed (Investigation Findings)

### Before Investigation
"Jeli adds security to memory. But so does encrypting OB1. Why build it?"

### After Investigation  
"Jeli adds three things uniquely:
1. **Tiering** — memory scales without bloating token budget
2. **Curation** — automatically keeps important facts accessible
3. **Governance** — security enforced by architecture"

Competitors (mem0, Letta) have memory. **Jeli has intelligent, trustable, tiered memory.**

---

## New Documents Created

### 1. TECHNICAL-SPECIFICATION.md
**What:** Concrete schema design for L0/L1/L2/L3 layers

**Key Details:**
- PostgreSQL L1/L2 table schema with hash-chain
- Redis L0 hot cache configuration
- MCP interface (scoped access for agents)
- Immutable audit log
- Curation fields (importance, frequency, salience, contradiction count)

**Use this to:** Understand database schema, implement migrations, design MCP tools

---

### 2. CURATION-ALGORITHM.md
**What:** How Jeli decides what matters

**Key Components:**
1. **Importance Scoring** — User marks facts 0-10
2. **Frequency Scoring** — Facts accessed often stay hot
3. **Salience Decay** — Older facts fade (half-life model)
4. **Contradiction Detection** — Poisoning defense
5. **Eviction & Promotion** — Automatic layer movement

**Use this to:** Implement scoring engine, design curation job, understand intelligent retrieval

---

### 3. DEPLOYMENT-PLAN.md
**What:** How Jeli actually runs on your Mac Mini

**Key Sections:**
- PostgreSQL 17 (local, port 5442)
- Redis setup (L0 cache)
- Python curation engine (hourly job via launchd)
- MCP server integration (with Claude Code)
- Health monitoring & alerting
- Operational runbooks (startup, backups, disaster recovery)

**Use this to:** Deploy to Mac Mini, monitor health, handle outages

---

## Architecture at a Glance

```
CAPTURE     → raw events (browser, apps, CLI, voice)
INGESTION   → dedup, classify, embeddings, hash-chain
TIERED STORAGE:
  L0 (Hot)    → RAM, <1ms, ~1MB, last 3-5 conversations
  L1 (Primary)→ PostgreSQL, 5-50ms, ~100MB, curated this-month
  L2 (Warm)   → PostgreSQL, 100-500ms, ~1GB, historical
  L3 (Cold)   → Files, 1-10s, unlimited, archive
INTELLIGENCE → curation, contradiction detection, conflict resolution
AGENT API    → MCP tools (scoped, secure)
```

---

## Three-Branch Governance (From Existing CLAUDE.md)

**Executive** (Agents: Hermes, Claude)
- Propose memories via MCP
- Cannot write directly to store
- Bound by judicial precedent

**Legislative** (PostgreSQL + Jeli)
- Canonical truth
- Append-only hash-chain
- Three tiers: Constitutional, Statutes, Case law

**Judicial** (Conflict Resolution)
- Arbitrates contradictions
- Uses trust scores, recency, authority, precedent
- User-appealable

**Constitutional** (User-Signed, Inviolable)
- Data stays local
- No PII leaves machine
- User veto on irreversible actions

---

## Implementation Timeline

### Phase 1 (PoC, 2 weeks)
- [ ] L0 + L1 PostgreSQL tables
- [ ] Hash-chain audit log
- [ ] Simple recall (L0/L1 only)
- [ ] MCP tools: recall, remember, get
- [ ] Eviction policy (L1 → L2)

**Success:** 10k test facts, recall latency <100ms, deployed on Mac Mini

### Phase 2 (Trust & Curation, 2 weeks)
- [ ] Importance scoring (user marks important)
- [ ] Frequency scoring (track access patterns)
- [ ] Salience decay (temporal invalidation)
- [ ] Eviction job (hourly cron)
- [ ] User commands: mark-important, delete, archive

**Success:** Curated memory (L1 avg significance >0.6), automatic layer movement

### Phase 3 (Production & Intelligence, 2-3 weeks)
- [ ] L2/L3 tiering, archive operations
- [ ] Contradiction detection (poisoning defense)
- [ ] Intelligent retrieval (context-aware escalation)
- [ ] Health monitoring & alerting
- [ ] Backup & disaster recovery

**Success:** All tiers working, zero data loss, contradictions detected

### Phase 4 (Integration, 1 week)
- [ ] Optional OB1 integration (Jeli as overlay)
- [ ] Production deployment checklist
- [ ] Operational runbooks tested

---

## Key Insights from Investigation

### Why Tiering?
Single-layer memory doesn't scale:
- Memory bloats unbounded (7,300 conversations in 2 years)
- Token costs explode (can't fit everything in context)
- Relevance collapses (important facts buried in noise)

**Tiering solves:** Keep hot facts in RAM/L1, archive cold facts to disk

### Why Curation?
Can't rely on users to manually manage thousands of facts.

**Automatic curation decides:**
- What's important (user marking + frequency + recency)
- When to move between layers (eviction policy)
- What to promote (access-driven learning)

### Why Contradiction Detection?
As of 2026, memory poisoning attacks are documented (MINJA, Microsoft, Palo Alto).

**Detection:**
- Vector search for similar facts
- LLM-based contradiction scoring
- Flag for Judicial review if high-trust fact contradicted

### Why Immutability?
Never overwrite facts → complete audit trail → no silent corruption.

**Model:** `revise()` creates new record, links via `supersedes`, old version remains in archive

---

## Comparison to Competitors

| Feature | Jeli | UMP | mem0 | Letta | OB1 |
|---------|------|-----|------|-------|-----|
| **Sovereignty** | ✓ | ✓ | 🟡 | 🟡 | ✓ |
| **Tiered Storage** | ✓ | ✗ | ✗ | ✗ | ✗ |
| **Curation** | ✓ | ✗ | ✗ | ✗ | ✗ |
| **Immutability** | ✓ | ✓ | ❓ | ❓ | ❌ |
| **Contradiction Detection** | ✓ | ✗ | ✗ | ✗ | ✗ |
| **Governance Tiers** | ✓ | ✗ | ✗ | ✗ | ✗ |
| **Cryptographic Integrity** | ✓ | ✓ | ✗ | ✗ | ✗ |

**Jeli is uniquely positioned on curation + tiering + governance.**

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **PostgreSQL bloat (L1)** | Slow queries | Partitioning by month, archive to L3 |
| **Vector search latency** | Slow recall | Index only L1, not L2 (cost/benefit) |
| **Curation accuracy** | Wrong layer placement | Tune weights, user override via manual commands |
| **Backup failure** | Data loss | Daily backups, test restore quarterly |
| **Hash-chain break** | Audit log corruption | Verify integrity on startup, immutable append-only design |

---

## Success Metrics (Phase 1)

```
✓ PostgreSQL + Redis running on Mac Mini
✓ L0/L1 tables created, migrations clean
✓ Hash-chain audit log appending correctly
✓ Simple recall finds facts in <100ms
✓ MCP tools callable from Claude Code
✓ 10,000 test facts stored + retrieved
✓ Eviction job runs hourly, no errors
✓ Health check passes all conditions
✓ Backup created, restore tested
```

---

## Decision Points for JP

### 1. Proceed with Phase 1?
**Investigation answer:** Yes, solid technical foundation.  
**Your decision needed:** Time available? Resource priority?

### 2. Build standalone or integrate OB1?
**Investigation answer:** Standalone MVP (Phase 1-3), optional OB1 integration (Phase 4).  
**Your decision needed:** Want full stack or just memory?

### 3. Scope: MVP or Full?
**Investigation answer:** MVP is Phase 1-2 (4 weeks, L0/L1 + curation).  
**Your decision needed:** Ship early or wait for full stack?

---

## Quick Reference Links

**Investigation Files:**
- `/Volumes/MAC_MINI_1TB/universal-memory-protocol/jeli-research/INVESTIGATION-SYNTHESIS.md` — Full decision document
- `jeli-research-index.md`, `jeli-tiered-architecture.md`, etc. in Obsidian vault

**Implementation Files (This Directory):**
- `TECHNICAL-SPECIFICATION.md` — Schema, SQL, design
- `CURATION-ALGORITHM.md` — Scoring, eviction, intelligence
- `DEPLOYMENT-PLAN.md` — Mac Mini setup, operations, backups
- `CLAUDE.md` — Existing governance architecture
- `README.md` — Project overview

**Kanban:**
- Linked as working item: `[[jeli-research]]` with all references

---

## Next Actions

1. **Review Findings** (you're doing this now)
2. **Decide:** Proceed with Phase 1?
3. **If YES:** Pick starting point:
   - Option A: Create database schema (TECHNICAL-SPECIFICATION.md)
   - Option B: Implement scoring (CURATION-ALGORITHM.md)
   - Option C: Deploy stack (DEPLOYMENT-PLAN.md)
4. **If NO:** Document reasons, close investigation

---

## Questions?

All three new documents (A, B, C) are self-contained but cross-referenced. Start with whichever answers your most pressing question:

- **"What does the database look like?"** → TECHNICAL-SPECIFICATION.md
- **"How does Jeli decide what matters?"** → CURATION-ALGORITHM.md
- **"How do I run this on my Mac?"** → DEPLOYMENT-PLAN.md

---

**Investigation complete. Ready to build?**

