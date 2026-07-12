# Quarantine Lab and Synthetic Test Contract

> **Status: PROPOSED FOR REVIEW.** This document turns issue #48 into testable
> invariants and staged work. It does not describe functionality that ships
> today, and acceptance of this document does not complete the implementation.

## Purpose

Jeli's current quarantine is a read-time warning around content that already
lives in `memory_entry` and shares its vector index. That warning helps an agent
interpret a retrieved record, but it does not isolate retrieval geometry.

The proposed design creates three storage domains:

| Domain | Purpose | Production retrieval visibility |
|---|---|---|
| Production | ordinary governed memory | visible through normal gates |
| Hazardous | suspected injection, poisoning, or malware samples | never visible |
| Synthetic | signed, disposable test batches | never visible outside its batch |

The boundary is structural: separate tables and indexes, explicit database
roles, and namespace-scoped daemon state. Metadata labels alone do not satisfy
this contract.

## Non-negotiable invariants

1. Hazardous content is never embedded into the production vector index after
   it has been routed to the hazardous domain.
2. The agent-facing database role cannot read hazardous content or analyzer
   output. It may invoke only a narrow insert-only routing operation.
3. A lab analyzer cannot call tools, reach the network, read production memory,
   or write production memory.
4. Analyzer output is structured observation, not an instruction or an
   automatic promotion decision.
5. A synthetic authorization changes only where state is stored. It never
   relaxes classification, constitutional gates, trust caps, review rules, or
   failure behavior.
6. Synthetic state is isolated in every stateful subsystem, not only the memory
   table: chain, audit, graph, conflicts, precedent, daemon output, and queues.
7. Hazardous or synthetic data cannot be converted to production data by
   changing a flag, updating a row, or importing an ordinary export.
8. Routing and verification failures hold the input for human review. They do
   not fall back to production storage.

## Hazard routing

Routing is a state transition with an audit receipt:

```text
received -> production-candidate -> production
        \-> hazardous-candidate -> hazardous
        \-> held (router/classifier unavailable or uncertain)
```

Deterministic, high-confidence signals should route before semantic dedup or any
other production-index embedding. Later signals, including optional LLM
classification, must hold the inbox row until a privileged router transfers it
to the hazardous store. A transfer records the inbox identifier and content
digest, then removes raw content from the general queue according to a defined
retention policy.

The current implementation embeds inbox content against the production index
for dedup before its late LLM injection pass. The first implementation phase
must reorder or split that operation. Copying a held row after this step is
useful containment, but does not satisfy invariant 1 for known hazards.

False positives favor containment: a suspicious record waits for review rather
than entering production. Uncertainty never means "probably safe."

## Hazardous storage and roles

The initial schema should contain two append-only relations:

- `hazard_sample`: immutable content, content digest, source provenance,
  detection reason/version, routing receipt, timestamps, and optional lab-only
  embedding.
- `hazard_observation`: sample reference, analyzer identity/version, bounded
  verdict enum, confidence, signal codes, and a length-limited explanation.

Raw model transcripts do not belong in `hazard_observation`; they can reproduce
the payload and create a second injection surface.

Minimum role split:

| Role | Hazard sample | Hazard observation | Production memory |
|---|---|---|---|
| `jeli_app` | insert through narrow router only | none | existing scoped access |
| `jeli_lab_analyzer` | select | insert, select own results | none |
| `jeli_user` | reviewed operator access | reviewed operator access | user-tier access |
| migration owner | DDL | DDL | DDL |

If PostgreSQL cannot express the insert contract without exposing raw table
access, use a `SECURITY DEFINER` function owned by a dedicated no-login role.
The function must validate every argument, pin `search_path`, expose no dynamic
SQL, and return only a receipt. Its privilege tests are release-blocking.

## Analyzer isolation

The analyzer process receives one sample and returns a schema-validated result.
It runs with:

- no MCP or shell tools;
- no outbound network;
- credentials that cannot access production tables;
- a lab-specific model endpoint or local model with no shared conversation;
- bounded input/output sizes and hard timeouts;
- immutable analyzer version and prompt digest in every observation.

Allowed verdicts are deliberately narrow: `malicious`, `suspicious`,
`benign_candidate`, and `indeterminate`. No verdict automatically copies data to
production. A human may re-capture a benign candidate through the ordinary
user-tier write path; that new record cites the hazard sample digest and passes
all normal defenses.

## Signed synthetic batches

A batch begins with a signed manifest, not a caller-provided metadata field.
The manifest includes:

- batch UUID and nonce;
- issuer/key identity;
- issue and expiry timestamps;
- maximum record count and optional source allowlist;
- pipeline/configuration version under test;
- manifest schema version.

Signing authority remains outside the application process, preferably an
OpenBAO transit key or an offline asymmetric key. The runtime receives only a
verification capability. Verification is fail-closed, expiry and record limits
are enforced transactionally, and replaying a manifest cannot create a second
batch.

Every operation carries a server-owned storage namespace derived from the
verified manifest. The namespace is not accepted from tool arguments or caller
metadata. All internal repository methods require it explicitly so a daemon
cannot accidentally read across domains.

### Stateful isolation

Synthetic execution must use batch-scoped equivalents of:

- memory entries, state events, and chain heads;
- audit records;
- entity graph nodes, links, relations, and evidence;
- contradiction queues and judicial precedent/corroboration;
- inbox rows and daemon runs;
- derived insights and maintenance output.

Separate tables are preferred for the first implementation because role grants
and query review are easier to verify than pervasive `WHERE batch_id = ...`
predicates. If partitioning is adopted later, row-level security and forced RLS
are mandatory defense in depth, not a substitute for repository scoping.

Exports exclude hazardous and synthetic domains by default. A privileged test
export remains permanently marked and ordinary import rejects it. There is no
synthetic-to-production promotion operation.

## Delivery phases

### Phase 1: Insert-only hazardous store

- Create lab tables and roles after the current Alembic head is established.
- Route deterministic injection hits before production-index embedding.
- Transfer late classifier holds through the narrow router.
- Add live privilege tests proving `jeli_app` cannot select lab data and the
  analyzer cannot read or write production memory.
- Add retrieval tests proving lab vectors cannot appear in production search.

### Phase 2: Isolated analyzer

- Add the bounded observation schema and analyzer worker.
- Enforce runtime network/tool isolation outside application code.
- Add adversarial payload, timeout, malformed-output, and compromised-analyzer
  tests.

### Phase 3: Synthetic namespace

- Implement manifest verification and transactional batch limits.
- Make storage namespace explicit through every stateful repository API.
- Run mixed production/synthetic tests and prove production counts, graph,
  precedent, retrieval, and exports are unchanged after batch deletion.

## Acceptance tests

The implementation is not complete until automated tests prove:

1. A deterministically flagged payload produces no production embedding row.
2. Production semantic search cannot retrieve a lab or synthetic vector.
3. `jeli_app` SELECT on lab tables fails at the privilege layer.
4. Lab analyzer credentials cannot SELECT or INSERT production memory.
5. Router/analyzer failure holds data without a production fallback.
6. Forged, expired, replayed, or over-quota manifests fail closed.
7. Signed synthetic records traverse the same gates and trust caps as identical
   production candidates.
8. A mixed-load synthetic batch cannot change production precedent, graph,
   conflict queues, audit counts, chain head, daemon output, or export.
9. Ordinary import rejects hazardous and synthetic artifacts.
10. Human re-capture from a reviewed sample creates new provenance and does not
    preserve attacker-controlled trust or metadata.

## Residual risks

The triage router becomes a high-value target. A false-negative route can still
place hazardous content in production, and compromise of the migration owner or
user-tier credentials can cross database role boundaries. Resource-exhaustion
attacks can fill the lab or synthetic partitions. Model analysis remains
heuristic and attacker-influenced even when isolated.

These controls reduce retrieval and credential blast radius; they do not prove
content safe or eliminate the need for review, quotas, monitoring, backups, and
external key custody.
