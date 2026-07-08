# Jeli Design Directions (exploratory, not ratified)

> **Status: EXPLORATORY.** This document records an adversarial design
> discussion (2026-07-08) and the directions it surfaced. Nothing here is
> ratified or committed. It exists so the reasoning is durable and so future
> work has something concrete to argue with. Settled principles live in
> [trust-doctrine.md](trust-doctrine.md); the current, honest limitations live
> in [THREAT-MODEL.md](THREAT-MODEL.md).

## 0. Language commitment (the first conclusion)

Jeli does not "secure" the store, "guarantee" authenticity, or "prevent"
tampering. It makes those things *harder*, in layers that can be turned on and
off, and it makes tampering *evident* rather than impossible. All project
language should be corrected to match. The honest framing is a safe or a vault:
it is as safe as you make it, it is never one hundred percent, and no wording
should imply or approach a guarantee. "Sovereign, verified" overclaims;
"sovereign, to the degree you configure and defend it" is truthful. Jeli is a
pledge to address a known set of vulnerabilities with multiple, independently
toggleable layers, not a promise of safety.

There are no guarantees in security, only defense in depth and raised attacker
cost. A padlock sits inside a safe, inside a vault, inside a building, behind a
firewall, on an air-gapped and shielded network, and the last thing between the
data and the door is something unpredictable and unfriendly. Every layer buys
cost and time; none buys certainty. This document should be read in that spirit.

## 1. Expanded threat model

The v0.1 threat model points outward, at attackers poisoning the store. The
higher-value attacks come from other directions and should be treated as
first-class:

- **Attack the human, not the store.** Manipulate the user upstream so they
  enter poison at firsthand (highest) trust. The integrity layer then makes the
  planted belief durable, high-trust, and tamper-evident. Assume the human is
  the weakest link, through error, misuse, duress, or coercion.
- **Attack the key.** System integrity currently reduces to one secret. Its
  theft (malware, physical access, social engineering, a single leak) forges
  anything and it verifies. Single point of total compromise.
- **Attack trust in the tool.** Induce false positives in verification until the
  user learns to ignore it, or make defenses fail closed aggressively enough
  that the user disables them. Security that gets switched off is worse than
  none.
- **Attack the substrate.** The embedding model controls what corroborates what;
  the LLM injection classifier is itself an LLM and is manipulable; the
  dependency chain is upstream of every defense.
- **Attack the maintainer or distribution.** The more adopted Jeli is, the more
  valuable its repo, releases, and maintainers become as targets.
- **Adopt it against a subject.** Deploy "sovereign verified memory" where the
  deployer holds the keys and the subject believes they are sovereign. See
  section 3g.

## 2. High-risk questions to keep open

- Who holds the key, and what is the model when that party is compromised or
  malicious? (Custody, rotation-under-compromise, recovery.)
- What happens when the defenses lock the subject out of their own memory?
  (Availability; fail-closed as self-denial-of-service.)
- What is the harm model when high-trust data is confidently wrong?
- How does the system forget, and who is allowed to make it?
- Sovereignty for whom, and detectable how?

## 3. Directions surfaced (candidates, not commitments)

### a. No single point of failure

Assume a single key failure or a denial-of-service event is likely, and design
to mitigate the failure and shrink its blast radius rather than to prevent it.
Candidate mechanisms: sharding and distributed storage; replication of shards
across independent locations; components that are hard to take down
simultaneously; Postgres distributed / high-availability capabilities. No single
key should be able to lock the whole system out.

### b. Authenticated, identity-bound writes

Inserts carry a verifiable identity and context, not just content. The shape:
"principal U, on device D, at timestamp T" for a human, or "agent B, on node N,
at timestamp T" for a delegate, bound with a certificate rather than asserted in
the payload. Attribution becomes a checkable property of the write, consistent
with the doctrine that only the channel identity you provisioned is trustworthy.

### c. RBAC and data compartmentalization on reads

A read is not "return the matching rows." It is "who is asking, for what task,
at what clearance, and do their roles support this query," answered by returning
the *minimum data necessary* to satisfy the request. Need-to-know and
least-exposure by default, because a request may come from the subject or from
any of the subject's agents, for any purpose. This pairs with the existing
ReadGate but goes further, toward compartmentalized, role-scoped, minimally
sufficient responses.

### d. Multiple principals, one primary for now

The single-user model is a starting point, not the end state. The intended
picture is one consciousness with many delegates: the primary user, flanked by
agents (each with limited context, hence the least-necessary-data response
model), plus trusted family and friends, each with their own RBAC. Custody can
pass to heirs or successors on death or incapacitation. Near-term focus stays on
a single primary user with a group of trusted agents requesting scoped access;
multi-principal support is an explicit eventual requirement to design toward now.

### e. A fleet of daemons, as separate repositories

The daemon pattern mirrors delegated cognition, and each daemon is a candidate
for its own repository (for example, a researcher/verifier daemon that enriches
high-risk or high-value memories, an insights daemon that surfaces actionable
patterns, a vulnerability daemon that hunts for weaknesses). Non-negotiable
constraints from the doctrine: a daemon that reaches the network is itself an
attack surface and a poisoning vector, so its output is external-tier by
default, it is rate-limited and sandboxed, and it may enrich attribution or
flag, but never elevate trust.

### f. Governed forgetting

A right to redact, amend, or improve a memory is assumed and should be built in,
but the modification itself must pass through the three branches: proposed
(legislative), adjudicated (judicial), and executed (executive), so that
forgetting is governed rather than arbitrary. This reconciles the append-only
chain with a genuine, auditable path to change.

### g. Sovereignty as defense in depth, not as a guarantee

"Architectural guarantee" overstates what is possible; there are no guarantees.
The achievable goal is to make sovereign-to-the-subject the *default and the
path of least resistance*, and to make any deviation *detectable and costly*.
Candidate properties: key custody bound to the subject (ideally the subject's
own hardware, with social or threshold recovery so it is not one brittle
secret); unfiltered read as the subject's right (governance constrains agents,
never the owner's view of their own memory); detectability of who holds the
governing keys, so a non-sovereign deployment is at least visible; and a real
right to exit and to erase. None of these is a guarantee. Together they bias the
system, in depth, toward the subject.

## 4. Branding (open)

"Jeli" is distinctive and protectable but obscure. "Exocortex" is more
approachable but was coined publicly around 1998 to 2000 and has prior
commercial use as a company name, so it is descriptive, hard to own, and carries
real collision and trademark risk. Any product use needs a proper clearance
search, not a guess. A safer pattern is to keep "Jeli" or "LegionForge" as the
protectable mark and use "exocortex" descriptively (for example, "a sovereign
exocortex") rather than as the mark itself. Unresolved.

## 5. Not ratified

The author has ratified none of this and expects several more design
discussions before doing so. Treat every direction above as a hypothesis to
attack, not a plan to build.
