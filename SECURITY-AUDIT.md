<!-- ARCHIVED 2026-07-06: superseded by implementation — Phase 1 core has since shipped and the security gaps flagged here (Judicial layer, encryption at rest, etc.) have been substantially addressed by GH #9-#30; see docs/THREAT-MODEL.md for the current, maintained threat model -->

# Jeli Security Audit & Red-Team Analysis

**Date:** 2026-05-18  
**Status:** Phase 1 Core Infrastructure Review  
**Scope:** Scoped MCP + Hash-Chain + Trust System + Security Layer  
**Threat Landscape:** MINJA (2025), Microsoft Recommendation Poisoning, Palo Alto Indirect Prompt Injection

---

## Executive Summary

Jeli's Phase 1 core (hash-chain + trust scoring + contradiction detection + security layer) is **structurally sound but operationally incomplete**. The cryptographic integrity model is robust, but the system is **vulnerable to memory poisoning at the point of capture** and **has no Judicial-layer enforcement** until Phase 2.

**Acceptable Risks:**
- OpenAI API compromise (fallback to local Ollama in Phase 2)
- Alembic schema version discovery (no secrets embedded)
- Low-trust memory noise (contradiction detection mitigates)

**Unacceptable Risks:**
- Silent hash-chain breaks (design prevents, must be monitored operationally)
- Hermes unrestricted shell access before Scoped MCP enforcement
- Memory poisoning without detection (detection exists, but no judicial enforcement)
- Tampering of record_hash field in database (requires DBA privilege, mitigated by Judicial precedent layer)

---

## Threat Model (STRIDE)

### S — Spoofing

#### Threat: API Key Spoofing
- **Attack:** Attacker guesses or intercepts API key (`SCOPED_MCP_API_KEY`)
- **Current Defense:** 
  - `hmac.compare_digest()` prevents timing oracle
  - 256-bit entropy via `secrets.token_urlsafe(32)`
  - Validation before any tool execution
- **Gaps:**
  - Key stored in plaintext in environment (mitigated: `.env` is `chmod 600`)
  - HTTP pre-TLS allows cleartext interception on LAN (known gap per CLAUDE.md, requires VLAN/firewall)
  - No key rotation mechanism
- **Red-Team: Feasible on Shared LAN** (pre-TLS), **NOT feasible** with modern entropy + HMAC-SHA256
- **Risk Level:** MEDIUM (mitigated by environment isolation)

#### Threat: Memory Record Spoofing (Claim False Authorship)
- **Attack:** Attacker creates memory with `created_by: "jp"` and `created_at` = future date
- **Current Defense:**
  - `created_by` is set by MCP server from authenticated context (Discord user ID or agent name)
  - `created_at` uses `server_default=sa.func.now()` in Postgres (server timestamp, not client-supplied)
- **Gaps:** None in Phase 1 (if MCP auth is enforced)
- **Risk Level:** LOW (assuming MCP auth layer is operational)

#### Threat: Agent Spoofing (e.g., Hermes pretending to be Claude)
- **Attack:** Hermes connects to MCP with forged `source_agent: "claude"`
- **Current Defense:** None yet (Scoped MCP tool dispatcher not implemented)
- **Mitigation Path:** Scoped MCP (Task #12) will require agent registration + cryptographic signing
- **Risk Level:** HIGH (until Scoped MCP tool dispatcher with agent identity binding)

---

### T — Tampering

#### Threat: Hash-Chain Tampering (Modify Memory Content + Recompute Hash)
- **Attack:** Database compromise. Attacker modifies `content` field and recomputes `record_hash` to match
- **Current Defense:**
  - Chain key is **server-side secret** (not in database)
  - HMAC-SHA256 computation requires chain key
  - Attacker cannot recompute without chain key
- **Verification:** `jeli verify` command walks chain, recomputes all HMAC-SHA256 values
- **Risk Level:** VERY LOW (attacker must compromise both database AND chain key in server memory)

#### Threat: prev_hash Tampering (Break Chain Integrity)
- **Attack:** Database compromise. Attacker modifies `prev_hash` field to break chain continuity
- **Current Defense:**
  - **No forward pointer validation yet** (Phase 2 Judicial layer will enforce via precedent)
  - Detection via `jeli verify` will flag chain break immediately
  - Judicial layer (deferred) will arbitrate breaks
- **Operational Gap:** Chain break alarm exists but no automated resolution until Phase 2
- **Risk Level:** MEDIUM (detectable but no enforcement path until Judicial layer)

#### Threat: Embedding Poisoning (Use Wrong Embedding Model)
- **Attack:** Attacker modifies embedding vector to be from different model (e.g., swap 1536-dim with 768-dim OpenAI embedding)
- **Current Defense:**
  - `embedding_dimensions` field stores expected dimension (1536 for OpenAI)
  - Mismatch detected via `validate_embedding_dimensions()`
  - Re-embedding queries can identify stale embeddings
- **Gaps:** No cryptographic signature over embedding (Phase 2 enhancement)
- **Risk Level:** LOW (dimension mismatch detection catches most swaps)

#### Threat: Contradiction Table Tampering (Hide Conflicts)
- **Attack:** Database compromise. Attacker deletes rows from `memory_contradiction`
- **Current Defense:**
  - `memory_contradiction` is append-only (no UPDATE/DELETE exposed via MCP)
  - Audit trail in `memory_audit_log` tracks contradiction creation
- **Gaps:** If attacker has raw SQL access (DBA privilege), they can DELETE
- **Risk Level:** MEDIUM (requires DBA-level DB access, detectable via audit trail gaps)

---

### R — Repudiation (Denial of Action)

#### Threat: Agent Denies Creating Poisoned Memory
- **Attack:** Hermes captures malicious memory, later denies it via repudiation
- **Current Defense:**
  - Immutable audit log (`memory_audit_log`) with `actor`, `timestamp`, `action`
  - Each write includes `created_by` (Discord user ID / agent name)
  - Hash-chain provides non-repudiation at cryptographic level
- **Gaps:** Hermes could claim "my API key was stolen" (requires Judicial layer to arbitrate)
- **Risk Level:** LOW (strong audit trail, requires Judicial precedent for arbitration)

#### Threat: User Denies Confirming Memory (Trust Inflation)
- **Attack:** User confirms bad memory at trust 0.9, later claims they didn't
- **Current Defense:**
  - Audit log shows confirmation action with user ID
  - Amendment chain links old → new record
- **Gaps:** No cryptographic proof of user action (Web3-style signatures deferred to Phase 2)
- **Risk Level:** LOW (operational burden on Judicial layer, not cryptographic weakness)

---

### I — Information Disclosure

#### Threat: Memory Content Leakage (Database Compromise)
- **Attack:** Database breach. Attacker reads plaintext memory content
- **Current Defense:** None (encryption deferred to Phase 2)
- **Operational Mitigations:**
  - Database at `127.0.0.1:5442` (localhost only, requires local access)
  - No memory exported via API without MCP auth
- **Risk Level:** VERY HIGH (if database breached, content is readable)
- **Acceptable?** YES (encrypted at-rest is Phase 2; local-only DB is Phase 1 acceptable risk)

#### Threat: Embedding Vector Leakage (Fingerprinting)
- **Attack:** Embeddings are stored as floats in database; attacker reconstructs text via embedding inversion
- **Current Defense:** None (embedding inversion is hard but improving in research)
- **Mitigations:**
  - Embeddings stored only on server, not exported via API
  - OpenAI API calls made server-side, vectors not passed to client
- **Risk Level:** MEDIUM-LOW (theoretical, requires advanced ML inversion + DB access)

#### Threat: Metadata Leakage (Session ID, Timestamps, Trust Scores)
- **Attack:** Attacker infers user behavior from temporal patterns + trust score distributions
- **Current Defense:** None
- **Mitigations:**
  - Session IDs are UUIDs (no correlation to user ID in Phase 1)
  - Timestamps are necessary for temporal queries (cannot redact)
- **Risk Level:** MEDIUM (requires database + access to full history, but inference possible)

#### Threat: Logging Side-Channel (Structured Logs Contain PII)
- **Attack:** Logs capture memory content or user IDs; logs are exfiltrated
- **Current Defense:**
  - Structured logging uses `request_hash` (SHA256 of request) instead of full content
  - Memory IDs logged, not content
- **Gaps:** First 100 chars of content may be logged for debugging (TBD in logging implementation)
- **Risk Level:** MEDIUM (depends on logging configuration in Phase 2)

---

### A — Availability

#### Threat: Denial of Service (Embedding Service Down)
- **Attack:** Attacker DDoSes OpenAI API, stopping capture_memory tool
- **Current Defense:**
  - Fallback to FTS-only search (returns results without embedding)
  - Circuit breaker: 3 failed API calls → 60s grace period
  - Logging for monitoring
- **Gaps:** No local embedding fallback in Phase 1 (Ollama deferred to Phase 2)
- **Risk Level:** MEDIUM (impacts capture, not critical for read-only queries)

#### Threat: Database Connection Exhaustion
- **Attack:** Attacker opens many connections to Postgres, exhausts pool
- **Current Defense:**
  - `AsyncPostgresPool` with `max_size=20` (default)
  - Connection timeout + cleanup
- **Gaps:** No rate limiting on connections per actor
- **Risk Level:** LOW (small pool, local-only access, requires local attacker)

#### Threat: Memory Growth Attack (Capture Huge Memories)
- **Attack:** Attacker repeatedly captures 10MB memories, fills disk/memory
- **Current Defense:**
  - `sanitize_content(..., max_length=10000)` caps memory to 10KB
  - Embedding vectors fixed at 1536 floats (~6KB)
  - Total per record: ~16KB (manageable)
- **Risk Level:** VERY LOW (size-capped)

---

### N — Non-Repudiation

#### Threat: Weak Proof of Memory Origin
- **Attack:** Attacker claims memory came from external source, not them
- **Current Defense:**
  - Hash-chain proves chronological order (who wrote before whom)
  - `created_by` identifies source
  - Audit log documents all actions
- **Gaps:** No cryptographic proof that `created_by` was not spoofed (requires Scoped MCP agent binding)
- **Risk Level:** MEDIUM (requires Judicial + Scoped MCP for full non-repudiation)

---

## Attack Surface Analysis

### 1. **MCP Interface (Highest Risk)**
```
Hermes ──[HTTP/stdio]──> Scoped MCP Server ──> capture_memory, search_memory, audit_trail
```

**Vulnerabilities:**
- **Missing:** Agent identity binding (no proof that `source_agent: "hermes"` came from actual Hermes)
- **Missing:** Rate limiting per agent
- **Missing:** Tool ACL (Hermes can call any tool; should be restricted to memory tools only)
- **Acceptable Risk?** YES, if Hermes is trusted & runs in Docker sandbox (per CLAUDE.md Tier 1 mitigations)

### 2. **Embedding Service Integration (Medium Risk)**
```
Scoped MCP ──[HTTPS]──> OpenAI API ──> Embed content ──> Return 1536-dim vector
```

**Vulnerabilities:**
- **API Key Exposure:** If OpenAI API key is compromised, attacker can:
  - Embed arbitrary content (low impact, would be caught by trust scoring)
  - Incur costs (financial impact)
  - Potentially read embedding logs on OpenAI side (OpenAI policy: logs deleted after 30 days)
- **MITM Attack:** Pre-TLS interception on LAN (requires VLAN segmentation)

**Acceptable Risk?** YES for Phase 1 (Ollama in Phase 2 removes cloud dependency)

### 3. **Database (Highest Risk if Breached)**
```
Postgres at 127.0.0.1:5442 (local only)
├── memory_entry (plaintext content)
├── memory_audit_log (immutable)
└── memory_contradiction (append-only)
```

**Vulnerabilities:**
- **No Encryption at Rest:** Plaintext content on disk
- **No Row-Level Security:** DBA can read all records
- **No Audit Trail of DB Access:** PostgreSQL audit logs not configured
- **Backup Leakage:** Checkpoint backup (`data.backup_jeli_20260518_171717`) is plaintext copy

**Acceptable Risk?** YES for personal use (localhost-only, user controls physical access)  
**NOT Acceptable?** For multi-user or cloud deployment (requires encryption + RLS + TDE)

### 4. **File System (Local)**
```
~/.env                    # API keys (chmod 600)
.env.example             # Sanitized template
alembic/versions/...     # No secrets
src/jeli_scoped_mcp/...  # No secrets
```

**Vulnerabilities:**
- **API Key in Memory:** If process is memory-dumped, key is readable
- **.env Readable by Root:** `chmod 600` prevents other users, but root can read

**Acceptable Risk?** YES (filesystem permissions are standard)

### 5. **Memory Poisoning (Core Threat per CLAUDE.md)**
```
Hermes ──[Malicious Input]──> capture_memory ──[Poison]──> memory_entry
                                    ↓
                            Trust Scorer (0.4 = agent-inferred)
                                    ↓
                        Contradiction Detector (May miss subtle lies)
                                    ↓
                            Store with flag? (Judicial layer deferred)
```

**Attack Chains:**

| Attack | Input | Example | Detection | Mitigation |
|--------|-------|---------|-----------|-----------|
| **Direct Lie** | False memory | "JP is a CEO" (false) | Contradiction if conflicts | User confirms truth (amend to 0.9) |
| **Subtle Drift** | Gradual truth shift | "I like coffee" → "I prefer tea" (true preference change) | Semantic similarity + temporal | Amortized by time + recency |
| **Inference Confidence** | Agent states unverified fact | "JP has 10k lines of code" (guess) | Trust score 0.6 (agent-inferred) | Requires user confirmation to boost |
| **Behavioral Injection** | Infer from clicks | "JP prefers dark mode" (inferred from clicks) | Trust 0.4 (behavior) | Needs user confirmation |
| **Context Injection** | Poison via conversation | "JP wants to quit" (said in frustration) | Low trust + temporal relevance | Judicial precedent (Phase 2) |

**Risk Level:** MEDIUM (detection exists, enforcement deferred to Phase 2 Judicial layer)

---

## Red-Team Attack Chains (Feasibility Analysis)

### **Chain 1: Silent Memory Poisoning (Low Feasibility)**
```
1. Gain local access to machine (requires laptop theft or insider)
2. Break into Postgres (requires DBA privilege or crack auth)
3. Modify memory_entry.content + recompute record_hash
   ❌ FAILS: Attacker doesn't have chain_key (server-side secret)
4. Fallback: Modify prev_hash to break chain
   ✓ WORKS: Chain break detectable via jeli verify
   ⚠️ Alarm only; no auto-resolution until Judicial layer
```
**Feasibility:** MEDIUM (requires DB access + knowledge of schema, but detectable)

### **Chain 2: Hermes Compromise (High Feasibility if No Mitigations)**
```
1. Compromise Hermes process (RCE via Discord command injection)
2. Hermes connects to Scoped MCP with valid API key
3. Hermes calls capture_memory with poisoned input
   ✓ WORKS: Poison ingested (low trust 0.4 if behavior-inferred)
4. Hermes calls capture_memory again with reinforcing memory
   ✓ WORKS: Contradiction detector may miss subtle contradiction
5. Over time, JP's memory drifts without detection
```
**Feasibility:** HIGH (if Hermes has unrestricted shell access)  
**Mitigation (Per CLAUDE.md Tier 1):** Docker backend + Discord allowlist + Scoped MCP  
**With Mitigations:** Feasibility drops to MEDIUM-LOW

### **Chain 3: OpenAI API Key Compromise (High Feasibility)**
```
1. Attacker reads OPENAI_API_KEY from .env or server memory
   ✓ WORKS: Requires local access or memory dump
2. Attacker makes API calls to OpenAI (costly, logged)
   ✓ WORKS: Financial damage, but auditable
3. Attacker embeds malicious content as if from legitimate source
   ❌ FAILS: Poison still ingested with low trust (0.6 agent-inferred)
```
**Feasibility:** MEDIUM (local access required; impact is financial + audit trail)

### **Chain 4: Database Backup Exfiltration (High Feasibility)**
```
1. Attacker copies data.backup_jeli_20260518_171717/
   ✓ WORKS: Plaintext Postgres backup on filesystem
2. Attacker restores backup on their machine, reads all memories
   ✓ WORKS: No encryption at rest
```
**Feasibility:** VERY HIGH (if attacker has filesystem access)  
**Mitigation:** Encrypt backup, delete old backups, monitor filesystem

---

## Vulnerability Catalog

### **Critical (Fix Before Production)**

| ID | Vulnerability | Risk | Mitigation | Timeline |
|----|---|---|---|---|
| C1 | Hermes unrestricted shell access before Scoped MCP | Memory poisoning, system compromise | Implement Scoped MCP (Task #12) | Phase 1 → Phase 2 (blocking) |
| C2 | No encryption at rest (database) | Full plaintext if DB breached | Defer to Phase 2 (acceptable for personal use) | Phase 2 |

### **High (Fix in Phase 2)**

| ID | Vulnerability | Risk | Mitigation | Timeline |
|----|---|---|---|---|
| H1 | No agent identity binding in Scoped MCP | Agent spoofing (Hermes claims to be Claude) | Implement agent cryptographic signing (Task #12) | Phase 2 |
| H2 | No Judicial layer enforcement | Poisoned memories stay unresolved | Implement Judicial conflict resolution (Phase 2) | Phase 2 |
| H3 | No rate limiting per agent | Resource exhaustion via rapid captures | Implement token bucket (Task #12) | Phase 2 |
| H4 | Backup leakage (plaintext) | All memories readable if backup stolen | Encrypt backups, implement rotation | Phase 2 |

### **Medium (Monitor in Phase 1)**

| ID | Vulnerability | Risk | Mitigation | Timeline |
|----|---|---|---|---|
| M1 | Trust score alone insufficient for detection | Subtle poisoning may bypass contradiction detector | Implement Judicial precedent layer + user amending | Phase 2 |
| M2 | No TLS on localhost (LAN only) | MITM possible on shared LAN | Implement mTLS or VLAN isolation | Phase 2 |
| M3 | API key in environment (plaintext) | Readable by root or process introspection | Standard mitigation: `chmod 600`, consider HSM in Phase 3 | Phase 2 |
| M4 | No PostgreSQL audit logging | DB access not auditable | Enable pg_audit extension | Phase 2 |

### **Low (Acceptable Risks)**

| ID | Vulnerability | Risk | Mitigation | Timeline |
|----|---|---|---|---|
| L1 | Embedding vectors stored plaintext | Theoretical inversion attack | Vectors not exported; requires DB access | Monitor research |
| L2 | Session metadata leakage (timestamps, UUIDs) | Behavior inference from temporal patterns | Session IDs are UUIDs (no correlation); acceptable for personal use | N/A |
| L3 | Memory dimension mismatch not cryptographically signed | Attacker swaps embedding vectors | Detection via dimension validation catches most swaps | Phase 3 (crypto signature) |

---

## Acceptable Risk Thresholds

### **ACCEPT in Phase 1 (Personal Use, Localhost)**

✅ **Plaintext database on local machine**
- Justification: User controls physical access; backup is on same machine
- Boundary: Only acceptable for single-user, local deployment

✅ **No encryption at rest**
- Justification: Phase 2 enhancement; threat model assumes trusted local environment
- Boundary: Not acceptable for cloud or multi-user

✅ **Hermes with Docker sandbox + Discord allowlist**
- Justification: Docker provides process isolation; Discord allowlist limits blast radius
- Boundary: Unacceptable without *both* mitigations; shell access alone is too risky

✅ **OpenAI API key compromise impact (financial only)**
- Justification: Poisoned memories still marked low-trust; attacker incurs costs (detectable)
- Boundary: Acceptable if OpenAI key is rotated quarterly

✅ **Low-trust memory noise (0.3–0.6)**
- Justification: Trust scoring + contradiction detection + user amending mitigate
- Boundary: Acceptable because poisoned memories are low-trust by default; user is always arbiter

### **DO NOT ACCEPT (Unacceptable Risks)**

❌ **Silent hash-chain breaks**
- Why: Integrity layer is load-bearing; breaks mean undetectable tampering
- Requires: Operational monitoring of `jeli verify` in background

❌ **Hermes with unrestricted shell access (no Docker, no Scoped MCP)**
- Why: Attacker can read/write arbitrary files, poison memory, exfiltrate backups
- Requires: Docker backend + Scoped MCP enforcement before Hermes integration

❌ **Judicial layer absence during poisoning**
- Why: Contradictions flagged but unresolved; user must manually arbitrate
- Requires: Phase 2 implementation of Judicial precedent layer

❌ **No audit trail of database access**
- Why: DBA actions unlogged; hard to prove/disprove tampering
- Requires: PostgreSQL audit logging (pg_audit extension)

---

## Operational Security Recommendations

### **Pre-Production Checklist (Phase 1 → Phase 2 Gate)**

- [ ] Implement Scoped MCP agent identity binding (cryptographic proof of source_agent)
- [ ] Enforce Hermes Docker backend + Discord allowlist before connecting
- [ ] Enable PostgreSQL audit logging (`pg_audit` extension)
- [ ] Implement API key rotation schedule (quarterly minimum)
- [ ] Add monitoring for `jeli verify` (watch for hash-chain breaks)
- [ ] Secure backup strategy (encrypt backups, test restore, delete old backups)
- [ ] Document threat model in README
- [ ] Create security runbook (incident response for DB breach, hash-chain break, API key compromise)

### **Monitoring & Alerting**

| Alert | Trigger | Action |
|-------|---------|--------|
| Hash-chain break | `jeli verify` detects mismatch | Page on-call + begin forensics |
| Contradiction unresolved | Judicial ruling not issued within 7 days | Notify user + escalate to manual review |
| API key rotation overdue | Key age > 90 days | Rotate key immediately |
| Database size anomaly | Record count grows > 10x baseline | Investigate for DoS attack |
| Backup not encrypted | Backup file created without encryption | Notify + delete + recreate |

### **Incident Response Playbook**

#### **Scenario: Database Breach (Plaintext Memories Exfiltrated)**
1. Assume passwords compromised (treat as insider threat)
2. Rotate all API keys immediately
3. Review audit_log for unauthorized access (if pg_audit enabled)
4. Assess impact: Which memories were sensitive?
5. If production → notify affected parties
6. Implement encryption at rest (Phase 2)

#### **Scenario: Hash-Chain Break Detected**
1. Do NOT ignore (suggests DB tampering)
2. Identify first broken record via `jeli verify`
3. Check audit_log for suspicious DELETEs or UPDATEs
4. Determine if attacker had DB access or if corruption is legitimate
5. Restore from backup if breach confirmed
6. Implement change detection (trigger on record_hash update)

#### **Scenario: Hermes Memory Poisoning Suspected**
1. Review memories captured by Hermes in time window
2. Check trust scores (should be 0.4–0.6 for agent-inferred)
3. Look for contradictions flagged in memory_contradiction table
4. If subtle: Use Judicial precedent (Phase 2) to arbitrate
5. If obvious: User amends to high trust (0.9) with corrected fact
6. If patterned: Consider Hermes compromise; move to Docker backend

---

## Risk Scoring Matrix

```
        LIKELIHOOD
        Low    Med    High
    High  🟡     🔴     🔴
I   Med   🟢     🟡     🔴
M   Low   🟢     🟢     🟡
P
A
C
T
```

| ID | Threat | Likelihood | Impact | Score | Mitigation | Status |
|----|--------|-----------|--------|-------|-----------|--------|
| C1 | Hermes RCE + unrestricted access | High | High | 🔴 | Docker + Scoped MCP | Blocking |
| C2 | DB plaintext breach | Low (localhost) | High | 🟡 | Phase 2 encryption | Accepted Phase 1 |
| H1 | Agent spoofing | Medium | High | 🔴 | Scoped MCP ID binding | Phase 2 |
| H2 | Poison unresolved | Medium | Medium | 🟡 | Judicial layer | Phase 2 |
| M1 | Subtle contradiction miss | Medium | Medium | 🟡 | Monitor, user amending | Phase 1 monitoring |
| M2 | LAN MITM (API key) | Low (requires VLAN access) | High | 🟡 | TLS + VLAN segmentation | Phase 2 |
| L1 | Embedding inversion | Low (hard attack) | Medium | 🟢 | Monitor research | Research tracking |

---

## Conclusion

**Jeli Phase 1 is cryptographically sound but operationally incomplete.**

**What works:**
- Hash-chain integrity (prevents silent tampering)
- Trust scoring (flags low-confidence captures)
- Contradiction detection (surfaces conflicts)
- Security layer (API key validation, injection defense)

**What's missing (Phase 2+):**
- Judicial layer (arbitrates unresolved contradictions)
- Encryption at rest (plaintext database)
- Agent identity binding (Scoped MCP)
- Operational monitoring (hash-chain verification, audit alerts)

**For Personal Use (Phase 1 Acceptable):**
- Localhost-only database is acceptable (user controls access)
- Low-trust memory noise is acceptable (user is arbiter, contradiction detection flags issues)
- Hermes integration requires Tier 1 mitigations (Docker + Discord allowlist)

**For Production/Multi-User (Phase 2 Required):**
- Encryption at rest (data cannot be plaintext)
- Judicial precedent layer (contradictions must be arbitrated systematically)
- Scoped MCP with agent binding (no spoofing)
- TLS + VLAN isolation (network security)
- PostgreSQL audit logging (DBA access tracked)

---

## Red-Team Conclusion

**Most Direct Attack Path (Feasible in 1-2 hours with local access):**
1. Steal laptop with running Postgres
2. Extract `.env` file → get API keys
3. Connect to local Postgres (default user, no password if peer auth)
4. Read plaintext memories (game over for that user)
5. Optionally: Insert poisoned memories, delete audit trail

**Mitigation:** Encrypt filesystem + database, require password auth for Postgres, TLS for OpenAI API

**Most Subtle Attack Path (Feasible over weeks if Hermes is compromised):**
1. Compromise Hermes process (Discord command injection)
2. Capture low-trust poisoned memories (0.4–0.6)
3. Reinforce with contradicting memories
4. Over time, user's memory drifts without user noticing
5. Judicial layer (Phase 2) would catch this if implemented

**Mitigation:** Docker sandbox for Hermes + Scoped MCP agent binding + Judicial precedent layer

---

**Risk Assessment: MEDIUM-HIGH for this Phase 1. Acceptable for personal prototyping only.**

**Recommendation: Do not expose to production or untrusted networks until Phase 2 Judicial layer + encryption at rest are implemented.**
