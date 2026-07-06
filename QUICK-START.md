<!-- ARCHIVED 2026-07-06: superseded by implementation — see README for current state -->

# Jeli Quick Start: Next 48 Hours

**For JP to decide:** Proceed with Phase 1 PoC? (2-4 weeks, ~40 hours dev time)

---

## What You Have Now

✅ **Complete Investigation** (6 documents, cross-referenced)  
✅ **Technical Design** (schema, MCP interface, database)  
✅ **Curation Algorithm** (scoring, eviction, intelligence)  
✅ **Deployment Plan** (Mac Mini setup, operations, backups)  
✅ **Operational Runbooks** (startup, health checks, recovery)

---

## Decision Checklist

### If YES ("Let's build Phase 1"):

1. **Quick Setup (1-2 hours)**
   - [ ] Read `README-INVESTIGATION-FINDINGS.md` (15 min)
   - [ ] Skim `TECHNICAL-SPECIFICATION.md` schema (20 min)
   - [ ] Review `DEPLOYMENT-PLAN.md` PostgreSQL section (15 min)

2. **Prepare Mac Mini (1 hour)**
   - [ ] Create `/Volumes/MAC_MINI_1TB/jeli-data/` directory
   - [ ] PostgreSQL running on port 5442
   - [ ] Redis running on port 6379
   - [ ] Test both with health check script

3. **First Week (20 hours)**
   - [ ] Create database schema (from TECHNICAL-SPECIFICATION.md)
   - [ ] Run Alembic migrations
   - [ ] Implement L0 Redis cache wrapper
   - [ ] Write basic recall/remember tests

4. **Second Week (20 hours)**
   - [ ] Implement MCP server (jeli.recall, jeli.remember, jeli.get tools)
   - [ ] Basic eviction policy (L1 → L2 by date)
   - [ ] Health monitoring script
   - [ ] Deploy on Mac Mini, test 7 days

**Result:** L0 + L1 working, basic retrieval, deployed locally

---

### If NO ("Not now, but maybe later"):

- [ ] Archive all docs in `jeli-research/` for future reference
- [ ] Keep CLAUDE.md (governance framework) for other projects
- [ ] Document decision: why not building now?

---

### If UNSURE ("Need more info"):

Read these in order (takes ~1 hour):

1. `README-INVESTIGATION-FINDINGS.md` (executive summary)
2. internal research notes (why tiering?)
3. internal research notes (decision hub, all links)

Then come back and re-answer: "Is this worth 40 hours dev time?"

---

## 48-Hour Action Items

**Hour 1-4: Information Absorption**
- [ ] Read `README-INVESTIGATION-FINDINGS.md`
- [ ] Skim `TECHNICAL-SPECIFICATION.md` (focus: "Storage Schema" section)
- [ ] Note any questions

**Hour 5-8: Environment Setup**
- [ ] Create `/Volumes/MAC_MINI_1TB/jeli-data/` directory
- [ ] Verify PostgreSQL installed and running (port 5442)
- [ ] Verify Redis installed and running (port 6379)
- [ ] Run health check: `/Volumes/MAC_MINI_1TB/LegionForge-jeli/bin/health-check.sh`

**Hour 9-12: Schema Review**
- [ ] Read `TECHNICAL-SPECIFICATION.md` "Storage Schema" sections (L0, L1, L2, L3)
- [ ] Understand hash-chain immutability model
- [ ] Understand MCP scoped interface
- [ ] Review SQL schema, note any questions

**Hour 13-16: Deployment Plan**
- [ ] Read `DEPLOYMENT-PLAN.md` sections 1-4 (PostgreSQL, Redis, Python, MCP)
- [ ] Understand launchd plist structure for Mac
- [ ] Understand backup strategy
- [ ] Understand health monitoring approach

**Hour 17-24: Final Decision**
- [ ] Answer: "Is this the right direction for Jeli?"
- [ ] Answer: "Is Phase 1 (4 weeks) reasonable timeline?"
- [ ] Answer: "Should we skip OB1 integration or plan it?"
- [ ] Create issue/task in your project management system

**Hour 25-48: Either Start Coding OR Archive**
- **If proceeding:** Create GitHub issues from Phase 1 checklist, assign to self
- **If not proceeding:** Write decision memo, archive docs, close investigation

---

## Questions to Answer in 48 Hours

### About Architecture
- [ ] Do you understand why tiering (L0-L3) solves the scalability problem?
- [ ] Do you understand why curation (importance/frequency/decay) solves relevance?
- [ ] Do you understand the three-branch governance model (CLAUDE.md)?

### About Implementation
- [ ] Are you comfortable with PostgreSQL schema design?
- [ ] Are you comfortable implementing Python curation job?
- [ ] Are you comfortable writing MCP tools?
- [ ] Is 2-4 weeks a reasonable timeline?

### About Operations
- [ ] Are you comfortable managing PostgreSQL on Mac?
- [ ] Are you comfortable with launchd for persistent services?
- [ ] Do you understand the backup/restore process?
- [ ] Do you understand the health monitoring approach?

---

## If You Want More Detail Before Deciding

**On Tiering (Why it matters):**
→ Read internal research notes (maintainer's vault)

**On Curation (How it works):**
→ Read `CURATION-ALGORITHM.md` sections 1-4

**On Deployment (How it runs):**
→ Read `DEPLOYMENT-PLAN.md` sections 1-5

**On Competitors (Why not mem0/Letta/OB1):**
→ Read internal research notes (maintainer's vault)

**On Governance (Three-branch model):**
→ Read `CLAUDE.md` (existing Jeli architecture)

---

## Red Flags (If You See These, Ask Questions)

- [ ] "The schema is too complex" → Simplify L0/L1 for PoC, add L2/L3 later
- [ ] "MCP seems hard" → Use Node.js template, not Deno
- [ ] "PostgreSQL setup is complicated" → Use Homebrew, follow DEPLOYMENT-PLAN.md exactly
- [ ] "4 weeks is too long" → Focus on Phase 1 PoC (2 weeks), push Phase 2 later
- [ ] "I don't understand curation scoring" → Start simple (importance only), add frequency/decay in Phase 2

---

## Go/No-Go Decision Framework

**PROCEED with Phase 1 if:**
- ✓ You believe tiered memory (L0-L3) solves real scaling problem
- ✓ You believe automatic curation matters (relevance > size)
- ✓ You have 4-6 weeks available in next 2 months
- ✓ Local PostgreSQL + Redis setup doesn't scare you
- ✓ Building custom AI infrastructure excites you

**PAUSE & RECONSIDER if:**
- ✗ You just want "memory that works" (OB1 does this)
- ✗ You don't have dev time in next 2 months
- ✗ Cloud services feel simpler than local setup
- ✗ You're uncertain about architecture (re-read docs)
- ✗ PostgreSQL/Redis operations concern you

**CLOSE INVESTIGATION if:**
- ✗ Competitors (mem0/Letta) are "good enough"
- ✗ No budget for custom tooling
- ✗ Don't care about sovereignty (vendor cloud is fine)
- ✗ Jeli solves problem you don't have

---

## Next Checkpoint (After 48 Hours)

**Email/note to self:**
```
Decision: [PROCEED / PAUSE / CLOSE]

If PROCEED:
- Timeline: Phase 1 estimated ____ weeks
- Resources: ____ hours/week available
- Priority vs other work: [HIGH / MEDIUM / LOW]
- Start date: ________

If PAUSE:
- Reason: ________________
- Revisit when: ________

If CLOSE:
- Reason: ________________
- Preserving: [Docs location for future]
```

---

## File You'll Actually Use (Bookmark These)

Once you decide to proceed:

1. **For building:** `TECHNICAL-SPECIFICATION.md` (schema, MCP)
2. **For scoring:** `CURATION-ALGORITHM.md` (eviction, importance)
3. **For ops:** `DEPLOYMENT-PLAN.md` (Mac setup, monitoring)
4. **For overview:** `README-INVESTIGATION-FINDINGS.md` (when confused)
5. **For navigation:** `RESEARCH-INDEX.md` (find what you need)

All in `/Volumes/MAC_MINI_1TB/LegionForge-jeli/`

---

## Support Resources

**If you get stuck:**
- Check `RESEARCH-INDEX.md` "If Stuck" section
- Re-read relevant section of TECHNICAL-SPECIFICATION / CURATION-ALGORITHM / DEPLOYMENT-PLAN
- Review operational runbooks in DEPLOYMENT-PLAN.md
- Check Obsidian vault docs (research phase)

---

**You have all the information you need to decide.**

**Take 48 hours. Make the call. Then either start building or archive clean.**

