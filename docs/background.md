# Background: Why Jeli Exists, and Why It's Built This Way

This document records the driver behind Jeli and the reasoning behind its
design decisions, including the 2026-07 hardening round. The
[README](../README.md) says *what* Jeli is; [ARCHITECTURE.md](ARCHITECTURE.md)
says *how* it works; this says **why**. The epistemic foundation underneath all
of it, why the system can verify attribution but never truth, is in
[trust-doctrine.md](trust-doctrine.md).

---

## The driver

Personal AI memory is becoming the most valuable, and least protected,
data a person generates. Two forces make it urgent:

**1. Memory is being weaponized.** As of 2026, memory poisoning is no longer
theoretical. The MINJA attack (arXiv 2025) demonstrated 95%+ injection
success against agent memory under realistic conditions. Microsoft Security
documented "AI Recommendation Poisoning" being exploited commercially at
scale. Palo Alto Unit 42 showed indirect prompt injection persisting in
long-term agent memory through ordinary documents. OWASP now lists memory
poisoning as **ASI06** in its Top 10 for Agentic Applications. The defining
property of these attacks is *patience*: a poisoned memory waits, silently
steering decisions for weeks before anyone notices, if anyone ever does.

**2. Memory is being enclosed.** ChatGPT, Gemini, Claude, and Apple
Intelligence all gained persistent personal memory in 2025-2026, and every
one keeps it inside its own product. Accumulated memory is the strongest
switching cost in consumer AI. In 2026 the vendors even weaponized
portability itself: memory *import* tools (Anthropic's and Google's) are
one-way doors designed to acquire users, not to free them. Your memory is
the moat, and it is theirs, not yours.

Jeli's answer to both is the same mechanism: **structural guarantees instead
of promises.** A hash chain the storage operator cannot silently rewrite. A
trust score the writer cannot inflate. A constitutional layer the agent
cannot amend, because the amendment surface is physically absent from its
tool list. An export that carries its own tamper-evidence. Security enforced
by architecture is the only kind that survives a motivated insider, or a
compromised agent.

## Why a governance model, not a filter

Most memory-poisoning defenses are filters: scan the content, block what
looks bad. Filters fail two ways: adaptive attackers learn what evades
them, and blocking teaches the attacker *exactly* which phrasing got
through. Jeli deliberately never blocks on content: flagged content is
**trust-capped and quarantine-wrapped**, so the payload lands with no
authority and full audit visibility, and the attacker learns nothing.

The deeper reason is separation of powers. A single component that ingests,
stores, judges, and enforces is a single component to compromise. Jeli
splits the roles the way constitutions do: agents (executive) can only
propose; storage (legislative) is append-only by database grant, not by
politeness; conflict resolution (judicial) is deterministic, logged, and
precedent-bound; and the constitutional layer is signed by the user alone.
None of the branches can override its own constraints. The griot analogy in
the README is not decoration; a jeli is trusted because the *role* is
accountable, not because any individual promises to behave.

## The 2026-07 hardening round: what changed and why

In July 2026 we ran the project against the year's research wave and asked:
what are we forgetting? Three papers reshaped the roadmap.

### Trust laundering (the gap we actually had)

[TMA-NM (arXiv 2606.24322)](https://arxiv.org/pdf/2606.24322) machine-verified
(in TLA+) a result that invalidates a common assumption, one we held:
**write-time trust scoring plus content inspection is provably insufficient**,
because trust can be *laundered* through three channels: agent
summarization, trusted-tool echoes, and manufactured corroboration.

Jeli had the first channel live: the insights daemon's cluster synthesis
took memories (including quarantined, injection-flagged ones), rephrased
them through an LLM (which strips the regex-detectable patterns), and wrote
the result at a flat trust of 0.5. A 0.3 poisoned memory could ride the
"dreaming" loop and re-enter the store as a clean, unflagged insight.

**Decision:** derived content never outranks its weakest source. The
synthesizer now excludes flagged memories from its input entirely, inherits
`min(source trusts)` capped at the daemon base, and records `derived_from`
lineage, following the [MemLineage (arXiv 2605.14421)](https://arxiv.org/pdf/2605.14421)
pattern. Consolidation is now a trust *bottleneck*, never a trust *pump*.

### Procedure imitation (a different attack surface than facts)

[MemoryGraft (arXiv 2512.16962)](https://arxiv.org/abs/2512.16962) showed
that agents imitate retrieved *procedures* far more readily than they
believe retrieved *facts*: poisoning "how-to" experience memories induces
persistent behavioral drift, because retrieval surfaces the grafted
procedure whenever a similar task appears.

**Decision:** procedural memories below user-confirmed trust (effective
trust < 0.7) are wrapped at read time in a `<jeli:unverified-procedure>`
envelope, a structural do-not-imitate signal to the consuming LLM.
Flagged procedures keep the stricter quarantine wrap. Read-time only,
never stored, consistent with every other Jeli wrapper.

### Similarity gaming (provenance must participate in ranking)

MemoryGraft-class attacks win at the ranking stage: the poisoned entry is
*engineered* to be embedding-similar to future queries, and pure relevance
ordering (vector distance or LLM judgment) is blind to provenance.

**Decision:** safety-aware re-ranking. After relevance scoring, a
deterministic pass multiplies each result's score by a trust-derived weight
and slashes injection-flagged entries, so a poisoned memory with perfect
similarity still ranks below a moderately relevant trusted one. We chose
the deterministic form (no extra LLM call) as v1; an LLM entailment check
against constitutional rules can layer on later without changing the
contract.

### What we decided *not* to do

- **Capture breadth (browser extensions, app hooks) was cut from the
  near-term roadmap.** Capture is the layer where OS vendors win
  unconditionally. Jeli's differentiation is defense depth and
  verifiability; every capture hour is a defense hour lost.
- **We did not adopt Microsoft's Portable Agent Memory protocol**
  (BLAKE3 Merkle-DAG, Ed25519). Jeli's JSON-Lines + SHA-256 export is
  deliberately simpler: local-first, no enclave dependency, readable by a
  human with `jq`. Sovereignty includes being able to understand your own
  archive.

## Where Jeli sits in the memory landscape

The 2026 market is settling into three tiers:

1. **Platform memory** (ChatGPT/Gemini/Claude/Apple): the default for most
   users. Network effects make this a natural oligopoly; Jeli does not
   compete here.
2. **Plug-and-play external memory** (mem0, Zep/Graphiti, Letta): the MCP
   ecosystem made memory pluggable; these systems compete on retrieval
   quality benchmarks. Jeli deliberately does not compete on retrieval
   either; it *layers on* storage systems rather than rebuilding them.
3. **Sovereign, verifiable memory.** Small in users, high in stakes:
   security-conscious individuals, regulated deployments, and whoever
   defines what tamper-evident memory portability means when regulation
   arrives. **This is Jeli's tier**, and its wedge into the others is being
   the integrity layer they adopt, not the product that replaces them.

Plug-and-play is a solved problem (MCP won). *Trustworthy* plug-and-play is
not. That gap (cryptographic integrity, verifiable provenance, structural
user veto) is the project's reason to exist, and as of 2026 no platform
vendor and no memory startup ships it.

## References

- [MINJA: memory injection attack](https://arxiv.org/abs/2503.03704): the attack that motivated the project
- [TMA-NM: non-malleable origin-bound authority](https://arxiv.org/pdf/2606.24322): laundering channels, machine-checked separation theorem
- [MemLineage: lineage-guided enforcement](https://arxiv.org/pdf/2605.14421): trust inheritance for derived memories
- [MemoryGraft: poisoned experience retrieval](https://arxiv.org/abs/2512.16962): procedure imitation attacks and reranking defense
- [OWASP Agent Memory Guard (ASI06)](https://owasp.org/www-project-agent-memory-guard/): the standardization effort Jeli aligns with
- [The Memory Wars](https://arxiv.org/pdf/2508.05867): network effects and the three-tier market analysis
- [Griot / Jeli](https://en.wikipedia.org/wiki/Griot): the name's origin: oral historian, keeper of memory, mediator
- Angela Shelf Medearis (author) & Terea Shaffer (illustrator), [*The Singing Man: Adapted from a West African Folktale*](https://www.goodreads.com/en/book/show/3067748), Holiday House, first edition, September 1, 1994. ISBN-13: 978-0823411030. The personal inspiration behind the name; see the README's "Why Jeli?" section
