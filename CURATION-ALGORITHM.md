<!-- ARCHIVED 2026-07-06: superseded by implementation — see README for current state -->

# Jeli Curation Algorithm: Intelligent Memory Prioritization

**Version:** 1.0-draft  
**Date:** 2026-06-06  
**Status:** Algorithm Design (Implementation follows Phase 2)  
**Related:** TECHNICAL-SPECIFICATION.md

---

## Problem Statement

Storing everything equally is **not viable**:
- Memory bloats unbounded (7,300+ conversations in 2 years)
- LLM context windows fill with noise (low signal-to-noise)
- Token costs explode (every query loads irrelevant data)
- Humans can't manage gigabytes of undifferentiated facts

**Solution:** Automatic + manual curation that keeps important facts in hot/warm layers and archives the rest.

---

## Core Insight: Three Signals

Jeli decides what matters by weighing three signals:

1. **Importance** — User explicitly marks it important (0-10 score)
2. **Frequency** — Facts accessed often are more valuable
3. **Recency** — Recent facts are more relevant (with temporal decay)

**Formula (simplified):**
```python
significance = (
    importance * 0.4 +        # What user says matters
    frequency * 0.3 +         # What user accesses often
    recency * 0.3             # What happened recently
)
```

Facts with high significance stay in L1 (warm, searchable).  
Facts with low significance move to L2 (cool, archived).  
Never delete; always preserve complete history.

---

## Component 1: Importance Scoring

### User-Direct Importance
Users explicitly mark facts as important:

```
jeli mark-important "No two-factor on X service — use app password"
→ importance = 10 (user says critical)

jeli mark-important "Prefer pnpm over npm"
→ importance = 8 (user says important)

(defaults to 3, no explicit marking)
```

**Implementation:**
```python
class ImportanceScore:
    CRITICAL = 10      # "Do not forget"
    IMPORTANT = 8      # "Keep accessible"
    NOTABLE = 5        # "Nice to remember"
    DEFAULT = 3        # No marking
    LOW = 1            # Possibly outdated

    @staticmethod
    def compute_dynamic(
        user_marked: int,
        contradiction_count: int,
        trust_score: float,
    ) -> int:
        """
        Adjust user-marked importance based on contradictions + trust.
        """
        base = user_marked
        
        # Penalize contradictions (poisoning signal)
        if contradiction_count > 2:
            base -= min(base - 1, contradiction_count)
        
        # Penalize low-trust external facts
        if trust_score < 0.5:
            base -= 2
        
        return max(1, min(10, base))  # Clamp to [1, 10]
```

**Why this matters:** User says "this matters to me" → Jeli respects it.

---

## Component 2: Frequency Scoring

### Access Pattern Analysis

Facts accessed often are valuable; ignore noise.

```python
class FrequencyScore:
    """
    Track how many times a fact is accessed, normalize to [0, 1].
    """
    
    # Historical access thresholds
    ACCESSED_ONCE = 1
    ACCESSED_RECENT = 3        # accessed 3+ times in last 30 days
    ACCESSED_WEEKLY = 7        # accessed weekly for a month
    ACCESSED_DAILY = 30        # accessed nearly daily
    
    @staticmethod
    def compute(
        access_count: int,
        last_accessed_days_ago: int,
        max_recent_access_count: int = 100,
    ) -> float:
        """
        Score based on recent access frequency.
        Recent (< 30 days) weighted higher than old.
        """
        if access_count == 0:
            return 0.0
        
        # Boost recent access
        recency_boost = 1.0 if last_accessed_days_ago <= 30 else 0.5
        
        # Normalize access_count to [0, 1]
        frequency = min(access_count, max_recent_access_count) / max_recent_access_count
        
        return frequency * recency_boost
```

**Example:**
```python
# Fact: "Pnpm is preferred in this repo"

# Scenario 1: Accessed 30 times in last month, last accessed today
frequency_score = 1.0  # Accessed constantly, still relevant

# Scenario 2: Accessed 5 times, last accessed 2 weeks ago
frequency_score = 0.85  # Used frequently, still fresh

# Scenario 3: Accessed 1 time, 6 months ago
frequency_score = 0.25  # Accessed once, long ago, probably stale
```

**Why this matters:** Frequent patterns > isolated incidents.

---

## Component 3: Recency (Temporal Decay)

### Half-Life Decay Model

Older facts naturally decay in salience, but never disappear.

```python
class SalienceDecay:
    """
    Facts lose salience over time using half-life model.
    Half-salience every N days (configurable per fact type).
    """
    
    DECAY_HALF_LIFE_DAYS = {
        "procedural": 180,      # "How to X" — 6-month half-life
        "factual": 365,         # "X is true" — 1-year half-life
        "episodic": 90,         # "What happened" — 3-month half-life
        "preference": 365,      # "I prefer Y" — 1-year half-life
        "decision": 365,        # "We decided X" — 1-year half-life
    }
    
    @staticmethod
    def compute_salience(
        base_salience: float,
        days_since_creation: int,
        memory_kind: str,
    ) -> float:
        """
        Salience = base * (0.5 ^ (days / half_life))
        """
        half_life = SalienceDecay.DECAY_HALF_LIFE_DAYS.get(memory_kind, 180)
        
        # Exponential decay: halve every half_life days
        decay_rate = 0.5 ** (days_since_creation / half_life)
        
        return base_salience * decay_rate
    
    @staticmethod
    def is_stale(salience: float, threshold: float = 0.1) -> bool:
        """
        Fact is "stale" if salience dropped below threshold.
        Stale facts move to L2 (cool storage).
        """
        return salience < threshold
```

**Example (Episodic, 90-day half-life):**
```
Created:    salience = 1.0
After 90 days:   salience = 0.5
After 180 days:  salience = 0.25
After 360 days:  salience = 0.0625  ← moves to L2
After 2 years:   salience ≈ 0.004   ← candidate for L3 archive
```

**Why this matters:** New facts are relevant; old facts fade unless important.

---

## Component 4: Combined Significance Score

### The Master Formula

```python
class SignificanceScore:
    """
    Combine importance, frequency, recency into one score [0, 1].
    This determines layer placement (L0, L1, L2, L3).
    """
    
    @staticmethod
    def compute(
        importance: int,                    # 0-10
        frequency: float,                   # 0-1
        salience: float,                    # 0-1
        contradiction_score: float = 0.0,   # 0-1, penalty if contradicted
    ) -> float:
        """
        Final significance = weighted sum of three signals.
        """
        # Normalize importance to [0, 1]
        importance_norm = importance / 10.0
        
        # Weights (can be tuned)
        w_importance = 0.4
        w_frequency = 0.3
        w_salience = 0.3
        
        # Combine
        base_score = (
            (importance_norm * w_importance) +
            (frequency * w_frequency) +
            (salience * w_salience)
        )
        
        # Penalize contradictions (poisoning signal)
        penalty = contradiction_score * 0.2
        
        return max(0.0, min(1.0, base_score - penalty))
    
    @staticmethod
    def determine_layer(significance: float) -> str:
        """
        Place fact in appropriate layer based on significance.
        """
        if significance >= 0.8:
            return "L0"  # Hot: keep in RAM, refresh TTL
        elif significance >= 0.6:
            return "L1"  # Primary: curated, indexed, searchable
        elif significance >= 0.3:
            return "L2"  # Warm: historical, less-indexed
        else:
            return "L3"  # Cold: archive, rarely accessed
```

**Layer Thresholds:**
- **L0** (significance ≥ 0.8): Hot memory, immediate context, accessed today
- **L1** (significance 0.6-0.8): Primary, important, this month
- **L2** (significance 0.3-0.6): Historical, past months
- **L3** (significance < 0.3): Archive, rarely accessed

---

## Component 5: Eviction & Promotion Policies

### Automatic Eviction

**Scheduled job (hourly):**
```python
def evict_by_policy():
    """
    Move facts between layers based on significance.
    """
    # L1 → L2: old, low significance
    for fact in facts_in_l1:
        if fact.significance < 0.6 and fact.days_in_l1 > 30:
            move_to_l2(fact)
    
    # L2 → L3: very old, not accessed
    for fact in facts_in_l2:
        if (fact.significance < 0.3 and 
            fact.days_in_l2 > 180 and
            fact.last_accessed_days_ago > 90):
            move_to_l3(fact)
    
    # L0 → L1: cache expired, not pinned
    for fact in facts_in_l0:
        if fact.ttl_expired and not fact.user_pinned:
            move_to_l1(fact)
```

### Automatic Promotion

**Access-driven promotion:**
```python
def on_recall_hit(fact_id):
    """
    User accessed this fact. Promote if valuable.
    """
    fact = get_fact(fact_id)
    
    # Increment access count
    fact.access_count += 1
    fact.last_accessed = now()
    
    # Recalculate significance
    fact.significance = SignificanceScore.compute(
        importance=fact.importance,
        frequency=FrequencyScore.compute(fact.access_count, ...),
        salience=SalienceDecay.compute_salience(fact.base_salience, ...),
    )
    
    # Promote if now significant
    if fact.current_layer == "L2" and fact.significance >= 0.6:
        move_to_l1(fact)
    
    if fact.current_layer == "L1" and fact.significance >= 0.8:
        promote_to_l0(fact)  # Pin to hot for 1 hour
    
    save(fact)
```

---

## Component 6: User-Driven Curation Commands

### Interface

```bash
# Mark important (stays in L1, won't evict)
jeli mark-important <fact-id>

# Unmark (can evict again)
jeli unmark-important <fact-id>

# Delete (move to L3 tombstone, full history preserved)
jeli delete <fact-id> "<reason>"

# Restore (bring back from L3)
jeli restore <fact-id>

# Archive (move to L3 immediately)
jeli archive <fact-id> "<reason>"

# Pin (keep in L0 cache for N hours)
jeli pin <fact-id> [hours=24]

# Unpin (stop pinning)
jeli unpin <fact-id>

# Export (create portable .json export of date range)
jeli export --from 2026-01-01 --to 2026-06-06 --out export.json
```

---

## Component 7: Contradiction Scoring (Poisoning Defense)

### Detect Memory Poisoning

When writing a new fact, check if it contradicts existing high-trust facts.

```python
class ContradictionDetector:
    """
    Detect poisoned or conflicting facts.
    """
    
    @staticmethod
    def find_contradictions(
        new_fact_embedding: Vector,
        new_fact_text: str,
    ) -> List[Contradiction]:
        """
        Search L1 for similar facts with opposite meaning.
        Return contradiction list + confidence.
        """
        contradictions = []
        
        # Vector search: find semantically similar facts
        similar_facts = vector_search(
            embedding=new_fact_embedding,
            limit=20,
            similarity_threshold=0.85,
        )
        
        for similar_fact in similar_facts:
            # Semantic check: does it contradict?
            contradiction_score = measure_contradiction(
                new_fact_text,
                similar_fact.text,
            )
            
            if contradiction_score > 0.7:  # 70%+ contradiction
                contradictions.append(Contradiction(
                    existing_id=similar_fact.id,
                    contradiction_score=contradiction_score,
                    existing_trust=similar_fact.trust_score,
                ))
        
        return contradictions
    
    @staticmethod
    def measure_contradiction(text_a: str, text_b: str) -> float:
        """
        LLM-based semantic contradiction scoring.
        Returns 0 (no contradiction) to 1 (definite contradiction).
        """
        # Prompt LLM to assess contradiction
        prompt = f"""
        Do these two statements contradict each other?
        
        Statement 1: {text_a}
        Statement 2: {text_b}
        
        Respond with a score 0.0-1.0 where:
        0.0 = no contradiction, fully compatible
        0.5 = ambiguous, possibly contradictory
        1.0 = definite contradiction
        """
        
        # Use Claude (local or remote)
        response = llm.classify(prompt)
        return float(response)
```

**On Contradiction Detected:**
```python
def on_contradiction_detected(
    new_fact,
    contradictions: List[Contradiction],
):
    """
    Surface to Judicial layer for resolution.
    """
    # If new fact is high-trust (user-direct)
    if new_fact.trust_score >= 0.9:
        # Auto-resolve: user override, old fact is stale
        for contradiction in contradictions:
            mark_stale(contradiction.existing_id, "superseded by user correction")
    else:
        # If fact is agent-inferred (low trust)
        if new_fact.trust_score < 0.7:
            # Flag for Judicial review
            surface_to_judicial({
                "type": "contradiction",
                "new_fact_id": new_fact.id,
                "existing_facts": contradictions,
                "recommendation": "resolve via precedent",
            })
```

---

## Implementation Strategy

### Phase 2a: Scoring Engine (1 week)
- [ ] Implement importance, frequency, salience classes
- [ ] Combine into SignificanceScore
- [ ] Test with 1k synthetic memories
- [ ] Tune weights based on real usage

### Phase 2b: Eviction Job (1 week)
- [ ] Hourly cron: score all L1 facts
- [ ] Move low-scoring to L2
- [ ] Move L0 expired cache to L1
- [ ] Test eviction doesn't lose data (verify audit log)

### Phase 2c: User Commands (1 week)
- [ ] CLI: mark-important, delete, archive, etc.
- [ ] MCP tools expose user-facing commands
- [ ] Test end-to-end (mark important → doesn't evict)

### Phase 3: Contradiction Detector (2 weeks)
- [ ] Semantic similarity search
- [ ] LLM-based contradiction scoring
- [ ] Judicial integration (surface conflicts)
- [ ] Test against MINJA/poisoning scenarios

---

## Metrics & Monitoring

```python
# Track curation health
metrics = {
    "avg_significance_l1": 0.72,        # Should be > 0.6
    "evictions_per_day": 45,            # Expected based on data size
    "promotions_per_day": 12,           # Access-driven
    "contradictions_detected": 3,       # Poisoning attempts caught
    "user_corrections": 8,              # User overrides engine
    "avg_layer_access_latency": {
        "L0": "0.5ms",
        "L1": "25ms",
        "L2": "250ms",
        "L3": "2000ms",
    },
}
```

---

## Example: Curation in Action

```
Scenario: JP uses Jeli for 6 months

Month 1:
  - Writes 500 facts (memories, preferences, learnings)
  - All start at significance=0.5 (default)
  - All in L1 (recently created)

Month 2:
  - Accesses "Use pnpm in repo" fact 15 times
  - frequency_score → 0.9
  - significance → 0.72 (staying in L1)
  - Engine doesn't evict it

  - "Random conversation from May" accessed 0 times
  - frequency_score → 0.0
  - significance → 0.2
  - Engine moves to L2

Month 3:
  - "Use pnpm" accessed 25 more times
  - User marks "IMPORTANT" (importance=10)
  - significance → 0.92
  - Engine promotes to L0 (hot cache)
  - When asked "package manager?", found instantly in L0

Month 6:
  - Lots of old facts accumulated in L2
  - Significance < 0.3, not accessed in 90 days
  - Engine moves to L3 (cold archive)
  - User can still retrieve via "jeli restore" if needed
  - But not cluttering hot layers

Result:
  - L0: ~10 facts (hot, accessed today)
  - L1: ~150 facts (important, this month)
  - L2: ~1,000 facts (historical, older)
  - L3: ~5,000 facts (archive, rarely accessed)
  - Total memory: searchable but organized by relevance
```

---

## Success Criteria

- [ ] Curation engine implemented, tested
- [ ] Eviction job runs hourly, no data loss
- [ ] Average L1 significance ≥ 0.6 (curated, not bloated)
- [ ] L0 latency <1ms, L1 <50ms sustained
- [ ] User can mark important/delete/archive
- [ ] Contradictions detected (0.7+ correlation with poisoning)
- [ ] All facts preserved (L3 archive is complete)

