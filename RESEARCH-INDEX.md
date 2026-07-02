# Jeli Research Index: Complete Navigation Guide

**Last Updated:** 2026-06-06  
**Investigation Status:** COMPLETE  
**Implementation Status:** Ready for Phase 1 PoC

---

## 📁 Document Organization

### Investigation Phase (Complete)
Research and decision-making documents. **Read these first to understand WHY Jeli.**

| Document | Location | Purpose |
|----------|----------|---------|
| **Investigation Synthesis** | `/Volumes/MAC_MINI_1TB/universal-memory-protocol/jeli-research/INVESTIGATION-SYNTHESIS.md` | Why build Jeli? Why standalone not OB1? Full decision doc (2,500 words) |
| **Tiered Architecture** | internal research notes (maintainer's vault) | Deep dive: why tiering solves scalability + relevance. Architecture decision. |
| **OB1 Research** | internal research notes (maintainer's vault) | Analysis: OB1 has destructive mutations (gap). Why not extend. |
| **UMP Research** | internal research notes (maintainer's vault) | Lessons from Universal Memory Protocol. What to adopt. |
| **Competitor Analysis** | internal research notes (maintainer's vault) | mem0/Letta gaps. Why they don't solve curation. |
| **Research Hub** | internal research notes (maintainer's vault) | Working item with all research links. Your main reference. |

---

### Implementation Phase (In Progress)

Implementation guides. **Read these to understand HOW to build Jeli.**

| Document | Location | Purpose |
|----------|----------|---------|
| **README-INVESTIGATION-FINDINGS** | `README-INVESTIGATION-FINDINGS.md` | Executive summary. Start here. Links to all others. |
| **TECHNICAL SPECIFICATION** | `TECHNICAL-SPECIFICATION.md` | Database schema (L0/L1/L2/L3), SQL, MCP interface, immutability model. |
| **CURATION ALGORITHM** | `CURATION-ALGORITHM.md` | How Jeli scores importance, decay, frequency. Eviction/promotion policies. |
| **DEPLOYMENT PLAN** | `DEPLOYMENT-PLAN.md` | How to run on Mac Mini. PostgreSQL, Redis, launchd, backups, operations. |
| **CLAUDE.md** | `CLAUDE.md` | Existing architecture. Three-branch governance, four-layer stack. |
| **README.md** | `README.md` | Project overview. Problem statement, threat model, OB1 integration. |

---

## 🗺️ Navigation by Goal

### "I want to understand the big picture"
1. Read `README-INVESTIGATION-FINDINGS.md` (10 min)
2. Read `CLAUDE.md` (existing architecture) (5 min)
3. Read internal research notes (maintainer's vault) (15 min)

**Total time:** ~30 minutes. You'll understand what Jeli is and why it's worth building.

---

### "I want to understand the technical design"
1. Read `TECHNICAL-SPECIFICATION.md` section 1-4 (database schema)
2. Read `CURATION-ALGORITHM.md` sections 1-3 (scoring logic)
3. Skim `DEPLOYMENT-PLAN.md` section 1 (architecture diagram)

**Total time:** ~1 hour. You'll understand the design well enough to implement Phase 1.

---

### "I want to implement Phase 1 PoC"
1. Follow `TECHNICAL-SPECIFICATION.md` → SQL schema + migrations
2. Follow `CURATION-ALGORITHM.md` → Implement scoring (Phase 2a)
3. Follow `DEPLOYMENT-PLAN.md` → Set up PostgreSQL, Redis, launchd
4. Implement MCP server (TECHNICAL-SPECIFICATION.md section "Scoped MCP Interface")

**Total time:** ~2-3 weeks. You'll have L0+L1 working locally.

---

### "I want to understand what to monitor"
1. Read `DEPLOYMENT-PLAN.md` → Health Monitoring section
2. Read `CURATION-ALGORITHM.md` → "Contradiction Scoring" section
3. Skim `TECHNICAL-SPECIFICATION.md` → Hash-Chain & Immutability

**Total time:** ~20 minutes. You'll know what can break and how to detect it.

---

### "I want to understand operational risks"
1. Read `DEPLOYMENT-PLAN.md` → Disaster Recovery Plan
2. Read `DEPLOYMENT-PLAN.md` → Operational Runbooks
3. Read `TECHNICAL-SPECIFICATION.md` → "Hash-Chain & Immutability"

**Total time:** ~15 minutes. You'll have a recovery plan.

---

## 📊 Decision Tree

```
"Should I proceed with Jeli?"
├─ YES: Is it Phase 1 PoC or full stack?
│  ├─ Phase 1 (4 weeks): L0+L1+basic curation
│  │  └─ Start: TECHNICAL-SPECIFICATION.md (schema)
│  └─ Full (8-9 weeks): L0-L3+curation+monitoring
│     └─ Start: TECHNICAL-SPECIFICATION.md (full design)
│
├─ UNSURE: Re-read what convinced me?
│  ├─ Tiering (scalability): jeli-tiered-architecture.md
│  ├─ Curation (relevance): CURATION-ALGORITHM.md
│  ├─ Governance (security): CLAUDE.md
│  └─ Why not competitors: OB1/mem0/Letta-research.md
│
└─ NO: Close investigation, archive for future reference
```

---

## 🔗 Cross-References

### Jeli's Security Foundation
- **Three-branch governance:** CLAUDE.md (existing)
- **Constitutional layer:** CLAUDE.md
- **Contradiction detection:** CURATION-ALGORITHM.md section 7
- **Hash-chain immutability:** TECHNICAL-SPECIFICATION.md section "Hash-Chain & Immutability"

### Jeli's Tiering Foundation
- **Why tiering?** README-INVESTIGATION-FINDINGS.md or jeli-tiered-architecture.md
- **L0-L3 details:** TECHNICAL-SPECIFICATION.md section "Storage Schema"
- **Eviction policy:** CURATION-ALGORITHM.md section 5
- **Deployment:** DEPLOYMENT-PLAN.md

### Jeli's Intelligence Foundation
- **Importance scoring:** CURATION-ALGORITHM.md section 1
- **Frequency scoring:** CURATION-ALGORITHM.md section 2
- **Salience decay:** CURATION-ALGORITHM.md section 3
- **Combined formula:** CURATION-ALGORITHM.md section 4

---

## 📋 Implementation Checklist

### Phase 1 (PoC, 2 weeks)

**Database:**
- [ ] PostgreSQL setup (DEPLOYMENT-PLAN.md)
- [ ] Create L0 Redis cache (DEPLOYMENT-PLAN.md)
- [ ] Migrations for L1 table (TECHNICAL-SPECIFICATION.md schema)
- [ ] Hash-chain audit log (TECHNICAL-SPECIFICATION.md)
- [ ] Test data load (10k facts)

**API:**
- [ ] MCP server scaffold (TECHNICAL-SPECIFICATION.md)
- [ ] jeli.recall() tool
- [ ] jeli.remember() tool
- [ ] jeli.get() tool

**Operations:**
- [ ] Eviction policy (L1 → L2) basic version
- [ ] Health check script (DEPLOYMENT-PLAN.md)
- [ ] Backup job (DEPLOYMENT-PLAN.md)

**Success Criteria:**
- [ ] All Phase 1 tests pass
- [ ] Recall latency <100ms L1
- [ ] Deployed on Mac Mini, stable 7 days
- [ ] Backup/restore tested

---

### Phase 2 (Curation, 2 weeks)

**Scoring:**
- [ ] Importance scoring (CURATION-ALGORITHM.md)
- [ ] Frequency scoring (CURATION-ALGORITHM.md)
- [ ] Salience decay (CURATION-ALGORITHM.md)
- [ ] Combined significance formula (CURATION-ALGORITHM.md)

**Eviction & Promotion:**
- [ ] Eviction job (hourly cron)
- [ ] Promotion on access
- [ ] Layer movement logic

**User Interface:**
- [ ] mark-important command
- [ ] delete command
- [ ] archive command
- [ ] restore command

**Success Criteria:**
- [ ] L1 avg significance > 0.6
- [ ] Eviction job runs hourly, no errors
- [ ] User commands work end-to-end
- [ ] Curation metrics tracked

---

### Phase 3 (Production, 2-3 weeks)

**Tiers:**
- [ ] L2 warm partition setup
- [ ] L3 cold archive storage
- [ ] Archive/restore operations

**Intelligence:**
- [ ] Contradiction detection
- [ ] Intelligent retrieval (context-aware escalation)
- [ ] Prefetching heuristics

**Monitoring:**
- [ ] Health dashboard
- [ ] Alert conditions
- [ ] Metrics collection
- [ ] Log aggregation

**Disaster Recovery:**
- [ ] Backup validation
- [ ] Restore testing
- [ ] Runbooks (startup, manual intervention)

**Success Criteria:**
- [ ] All tiers working
- [ ] Zero data loss scenarios
- [ ] Contradictions detected & logged
- [ ] Monitoring alerts functioning

---

### Phase 4 (Integration, 1 week)

**OB1 Integration (optional):**
- [ ] Design integration points
- [ ] Jeli as overlay to OB1
- [ ] Scoped MCP access control
- [ ] Test end-to-end

**Production Hardening:**
- [ ] Security audit
- [ ] Performance tuning
- [ ] Operational runbooks tested
- [ ] Deployment checklist

**Success Criteria:**
- [ ] OB1 integration (if chosen)
- [ ] All runbooks tested
- [ ] Ready for continuous operation

---

## 🎯 Key Metrics to Track

**Phase 1:**
- Recall latency by layer (target: L0 <1ms, L1 <50ms)
- Fact count growth (expect ~500 facts/month per user)
- Hash-chain validation (should always pass)

**Phase 2:**
- L1 average significance (target: >0.6)
- Eviction rate (facts/day moving to L2)
- Curation accuracy (user agrees with layer placement)

**Phase 3:**
- Contradiction detection rate (% of writes flagged)
- Poisoning scenarios caught (test with MINJA-like attacks)
- Backup success rate (100%)

---

## 📞 If Stuck

| Question | Answer Location |
|----------|-----------------|
| "What's the database schema?" | TECHNICAL-SPECIFICATION.md → Storage Schema |
| "How do I score importance?" | CURATION-ALGORITHM.md → Section 1 |
| "How do I deploy to Mac?" | DEPLOYMENT-PLAN.md → Component Deployment |
| "Why did we choose tiering?" | jeli-tiered-architecture.md or README-INVESTIGATION-FINDINGS.md |
| "What can go wrong?" | DEPLOYMENT-PLAN.md → Disaster Recovery |
| "How do I detect poisoning?" | CURATION-ALGORITHM.md → Section 7 |
| "Why not use OB1?" | OB1-research.md (Obsidian) |

---

## ✅ Pre-Implementation Checklist

Before starting Phase 1:
- [ ] Read `README-INVESTIGATION-FINDINGS.md`
- [ ] Review `TECHNICAL-SPECIFICATION.md` schema
- [ ] Review `CURATION-ALGORITHM.md` scoring
- [ ] Review `DEPLOYMENT-PLAN.md` architecture
- [ ] Understand three-branch governance (CLAUDE.md)
- [ ] Decide: Full stack or Phase 1 PoC?
- [ ] Allocate time: 2-4 weeks for Phase 1
- [ ] Set up Mac Mini environment (PostgreSQL, Redis)
- [ ] Create jeli-data directory for logs/backups

---

## 📚 Complete Document List

```
Investigation Phase (Obsidian vault):
  Library/AI/projects/
    ├─ jeli-research.md (working item hub)
    ├─ jeli-tiered-architecture.md (newest findings)
    ├─ jeli-research-index.md
    ├─ OB1-research.md
    ├─ UMP-research.md
    ├─ mem0-research.md
    └─ Letta-research.md

Investigation Phase (Universal Memory Protocol repo):
  /Volumes/MAC_MINI_1TB/universal-memory-protocol/
    └─ jeli-research/
       └─ INVESTIGATION-SYNTHESIS.md (2,500-word decision doc)

Implementation Phase (This directory):
  /Volumes/MAC_MINI_1TB/LegionForge-jeli/
    ├─ RESEARCH-INDEX.md (this file)
    ├─ README-INVESTIGATION-FINDINGS.md (start here!)
    ├─ TECHNICAL-SPECIFICATION.md (what to build)
    ├─ CURATION-ALGORITHM.md (how to prioritize)
    ├─ DEPLOYMENT-PLAN.md (how to run it)
    ├─ CLAUDE.md (existing architecture)
    └─ README.md (project overview)

Kanban:
  Library/Kanban/Kanban.md → Working list
    └─ [[jeli-research]] (linked working item)
```

---

## 🚀 Ready to Start?

**Option 1: Quick Orientation (30 min)**
1. Read `README-INVESTIGATION-FINDINGS.md`
2. Scan `TECHNICAL-SPECIFICATION.md` schema section
3. Skim `DEPLOYMENT-PLAN.md` architecture

**Option 2: Deep Dive (2-3 hours)**
1. Read all investigation files (Obsidian)
2. Read all implementation files (this directory)
3. Make Phase 1 implementation plan

**Option 3: Start Coding (assumes knowledge)**
1. Copy `TECHNICAL-SPECIFICATION.md` schema to migration files
2. Set up PostgreSQL + Redis (DEPLOYMENT-PLAN.md)
3. Implement MCP server
4. Test recall/remember

---

**Next step: What's your preference?**

