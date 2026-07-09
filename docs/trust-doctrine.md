# Jeli Trust Doctrine

This is the epistemic foundation the rest of Jeli answers to. Every trust
decision, every defense, and every future feature should be checkable against
the axiom below. It exists because "how much can we trust a memory?" is not an
implementation detail; it is the question the whole system is built to answer
honestly.

For the threat model see [THREAT-MODEL.md](THREAT-MODEL.md); for why the
project exists see [background.md](background.md).

---

## The axiom

**A memory system can verify integrity and attribution. It can never verify
truth.**

Cryptography does not tell you a claim is true. It tells you *who committed it*
and *that it has not changed since*. Authorship plus tamper-evidence. A liar can
sign a lie perfectly. So the job was never to know what is true; the job is to
never lose track of *whose claim this is and how far it sits from firsthand
knowledge*, so trust stays revisable when better evidence arrives.

Three distinct properties, only two of which any system can check:

| Property | Checkable? | By what |
|---|---|---|
| **Integrity**: did these bits change since they were committed? | Yes, objectively | hash chain |
| **Attribution**: who committed it? | Yes, objectively | signature / HMAC (requires a key) |
| **Accuracy**: does it correspond to reality? | No, never | corroboration and eventual contact with reality, always probabilistic |

Jeli owns the first two. Nothing owns the third. Any feature that claims to
establish accuracy is really doing corroboration, which is a probability, not a
proof.

## You never trust data. You trust sources.

Trust is not a truth-value stamped on a record. It is a calibrated bet on a
*source*, carried onto that source's claims by provenance. "Can we trust the
data" is the wrong question: data is only ever trusted as the shadow of an
authenticated, calibrated source. This is why Jeli is provenance-first. It is
not trying to know what is true; it is keeping every claim tied to its origin so
the bet can be re-judged later.

## The user is the sensor

In a personal memory system there is exactly one source of firsthand ground
truth: the human's direct assertion. That is why user-direct trust is 1.0 and
nothing else is. Every other trust value measures **verifiable distance from the
user's firsthand input**: agent-inferred is one hop out, external is further.
The entire trust lattice is "how far is this claim from the one real sensor."

Cryptographic provenance's job, stated in one line: **preserve how far each
claim sits from the user, and make that distance impossible to forge shorter.**

## Ingestion is witnessing. Import is hearsay.

At ingestion the system is present at the event. It witnesses the arrival on its
own clock, through an authenticated channel, and seals the claim with its own
key. That sealing is the closest thing the system has to a sensor reading: not
"this is true," but "I personally witnessed this claim enter through this
channel at this moment, and sealed it."

Restored or copied data has no such witnessing; the system was not there when the
record was made. So an import is hearsay *unless it can cryptographically
re-prove it was the system's own prior witnessing* (its record HMAC recomputes
under the local chain key). This is the justification for the import trust
ceiling: foreign data cannot inherit firsthand trust, because no one witnessed
it firsthand here.

## Verify versus support: the asymmetry

Two different mechanisms bear on trust, and conflating them is the classic
mistake:

- **Verify (authenticate)**: cryptographic, binary, unforgeable, requires a
  secret the attacker lacks. The only thing that may *raise* trust.
- **Support (corroborate)**: statistical and relational; cheap to manufacture at
  scale. A secondary signal only.

The rule that falls out:

> **Support can safely subtract trust. It can never safely add it.**

Flagging a memory that contradicts the established graph, has an implausible
timestamp, or clusters only with other freshly-arrived records is fail-safe:
downgrading on suspicion costs little. But "many records agree, so trust it
more" is unsafe, because an attacker who controls the input controls the
agreeing records too. That is the manufactured-corroboration channel.

Corroboration is legitimate Bayesian evidence about accuracy **only when the
corroborators are independent**, and independence itself can only be established
by authentication. So even the soft signal leans on the hard one: support
without authenticated independence is manufacturable, and therefore worthless
against an adversary. Cryptography sits upstream of everything, including the
"soft" checks.

## The decision rules

1. **Axiom.** The system verifies integrity and attribution, never truth. Trust
   is a calibrated bet on a source, carried by provenance.
2. **Ground truth is the user's direct input.** One sensor. Everything else is
   ranked by verifiable distance from it.
3. **Default untrusted.** A ceiling on all inbound content; trust is earned, not
   asserted. A ceiling and a floor are the same mechanism seen from two sides.
4. **Only three grounded events may raise trust, none of them payload-derived:**
   - the **user confirms** it (the sensor speaks) up to 1.0;
   - **cryptographic proof of self-authorship** (record HMAC recomputes under the
     local chain key) preserves the prior tier;
   - **independent, authenticated corroboration** accrued over time, in small,
     capped, slow increments only.
5. **Everything else is provenance-preserved, trust-capped, and revisable.** The
   system is not deciding truth now; it is keeping enough attribution to revise
   when the sensor eventually weighs in.

## The bedrock, stated plainly

You cannot ground truth, so do not try. Ground *attribution* instead, keep it
tamper-evident, cap trust to distance-from-the-user, and make everything
revisable. Descartes wanted one indubitable foundation and truth will not give
you one, but attribution will, because you hold the chain key.

**The cogito of a memory system is not "this is true." It is "I witnessed this
claim arrive, and I can prove it has not changed since."** That sentence is the
firm ground the whole trust model stands on.

---

## Open design question: attribution depth (not yet settled)

Everything above is settled doctrine. How *richly* to record provenance for
external claims is an open design question. What is already decided, because it
follows from the doctrine:

- **Match depth to risk, not to a fixed rule.** This is exactly
  [Wikipedia's actual policy](https://en.wikipedia.org/wiki/Wikipedia:Verifiability):
  no fixed number of sources per claim; contentious or high-impact claims demand
  more, routine ones demand little. Attribution effort should be proportional to
  how much a wrong belief would cost.
- **Independence is the load-bearing property**, not source count. Ten outlets
  owned by one entity are one source wearing ten hats. Triangulation across
  genuinely opposing owners/incentives is strong *because* an adversary would
  have to corrupt independent parties at once; triangulation across a monoculture
  is theatre. Any source-rating aggregation (the
  [Ground News](https://ground.news) or
  [schema.org ClaimReview / credibility-review](https://arxiv.org/pdf/2008.12742)
  pattern) is a **support** signal: it may lower confidence or flag, never
  cryptographically raise trust.
- **A source rating is itself an attributed claim.** Whoever rates a source is a
  source; their rating carries its own provenance and its own trust distance.
  Ratings do not bottom out in ground truth; they bottom out in more attribution.
- **A "researcher" daemon that fetches web evidence is itself an attack
  surface**, and a prime one: it is a direct path for large-scale AI source-data
  poisoning to enter the store. If built, its output is external-tier by default
  (it did not witness anything firsthand), rate-limited, and never a trust
  elevator.
- **Tier research effort to high-risk / high-value data.** Extensive automated
  citation-gathering costs tokens and adds attack surface, so it should be
  reserved for memories whose being wrong is expensive. Cheap routine memories
  stay cheaply attributed.

The likely shape, following [W3C PROV](https://www.w3.org/TR/prov-overview/)
(entity / activity / agent, domain supplies the verification semantics): store
for each external claim its source identity, locator (URL / DOI), retrieved-at
timestamp, and the extraction activity, with corroborating sources linked as
*support* edges annotated by independence. How deep the bibliography goes, and
whether a researcher daemon is worth its attack surface, are tracked as open
questions rather than settled here.
