"""jeli command-line interface."""

import argparse
import asyncio
import json
import sys
from typing import Any

from .config import Settings
from .database.pool import AsyncPostgresPool
from .portability import DEFAULT_IMPORT_TRUST_CEILING
from .tools.memory_tools import MemoryTools
from .tools.state_tools import StateTools


async def _run_verify(settings: Settings) -> dict:
    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        tools = MemoryTools(
            db=db, embedder=None, chain_key=settings.chain_key, key_id=settings.chain_key_id
        )
        result = await tools.verify_chain()
        state = StateTools(
            db=db, memory_tools=tools, chain_key=settings.chain_key, key_id=settings.chain_key_id
        )
        result.update(await state.verify())
        return result
    finally:
        await db.close()


async def _run_integrity_report(settings: Settings) -> dict:
    """Full integrity health report: chain validity + memory/trust/queue stats."""
    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        tools = MemoryTools(
            db=db, embedder=None, chain_key=settings.chain_key, key_id=settings.chain_key_id
        )
        state = StateTools(
            db=db, memory_tools=tools, chain_key=settings.chain_key, key_id=settings.chain_key_id
        )

        chain = await tools.verify_chain()
        state_result = await state.verify()

        by_type = await db.fetchall(
            """
            SELECT memory_type, COUNT(*) AS count
            FROM memory_entry WHERE valid_until IS NULL
            GROUP BY memory_type ORDER BY count DESC
            """
        )
        by_class = await db.fetchall(
            """
            SELECT metadata->>'content_class' AS content_class, COUNT(*) AS count
            FROM memory_entry WHERE valid_until IS NULL
            GROUP BY metadata->>'content_class' ORDER BY count DESC
            """
        )
        trust = await db.fetchrow(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE trust_score >= 0.8) AS high_trust,
                COUNT(*) FILTER (WHERE trust_score >= 0.5 AND trust_score < 0.8) AS medium_trust,
                COUNT(*) FILTER (WHERE trust_score < 0.5) AS low_trust,
                ROUND(AVG(trust_score)::numeric, 3) AS avg_trust
            FROM memory_entry WHERE valid_until IS NULL
            """
        )
        stuck = await db.fetchrow(
            """
            SELECT COUNT(*) AS stuck_conflicts
            FROM memory_conflict_queue
            WHERE status IN ('failed', 'processing')
              AND (status = 'failed' OR claimed_at < now() - interval '1 hour')
            """
        )
        orphaned = await db.fetchrow(
            """
            SELECT COUNT(*) AS orphaned_state_events
            FROM memory_state_event mse
            WHERE NOT EXISTS (
                SELECT 1 FROM memory_entry me WHERE me.id = mse.target_memory_id
            )
            """
        )
        no_audit = await db.fetchrow(
            """
            SELECT COUNT(*) AS memories_without_audit
            FROM memory_entry me
            WHERE NOT EXISTS (
                SELECT 1 FROM memory_audit_log mal WHERE mal.memory_id = me.id
            )
              AND valid_until IS NULL
            """
        )
        aging = await db.fetchrow(
            """
            SELECT COUNT(*) AS aging_high_trust
            FROM memory_entry
            WHERE valid_until IS NULL AND trust_score >= 0.8
              AND created_at < now() - interval '90 days'
            """
        )

        return {
            "chain_valid": chain["chain_valid"],
            "records_checked": chain["records_checked"],
            "first_bad_record": chain.get("first_bad_record"),
            "state_chain_valid": state_result.get("state_chain_valid"),
            "events_checked": state_result.get("events_checked"),
            "cache_consistent": state_result.get("cache_consistent"),
            "memory_stats": {
                "total": int(trust["total"]) if trust else 0,
                "by_type": {r["memory_type"]: int(r["count"]) for r in by_type},
                "by_content_class": {
                    (r["content_class"] or "unclassified"): int(r["count"]) for r in by_class
                },
            },
            "trust_distribution": {
                "high_trust": int(trust["high_trust"]) if trust else 0,
                "medium_trust": int(trust["medium_trust"]) if trust else 0,
                "low_trust": int(trust["low_trust"]) if trust else 0,
                "avg_trust": (
                    float(trust["avg_trust"])
                    if trust and trust["avg_trust"] is not None
                    else None
                ),
            },
            "stuck_conflicts": int(stuck["stuck_conflicts"]) if stuck else 0,
            "orphaned_state_events": (
                int(orphaned["orphaned_state_events"]) if orphaned else 0
            ),
            "memories_without_audit": (
                int(no_audit["memories_without_audit"]) if no_audit else 0
            ),
            "aging_high_trust": int(aging["aging_high_trust"]) if aging else 0,
        }
    finally:
        await db.close()


async def _run_re_embed(
    settings: Settings, dry_run: bool, batch_size: int, model: str | None
) -> dict:
    """Re-embed memories whose embedding_model differs from the current model.

    Re-embedding is a privileged, constitutional exception: it updates the
    derived index columns (embedding, embedding_model, embedding_dimensions,
    embedded_at) which are NOT part of the canonical record hash, so the chain
    stays valid."""
    from .embedding.provider import EmbeddingProvider

    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        embedder = EmbeddingProvider.from_settings(settings)
        target_model = model or embedder.model_id()

        if dry_run:
            count_row = await db.fetchrow(
                """
                SELECT COUNT(*) AS c FROM memory_entry
                WHERE valid_until IS NULL AND embedding_model != $1
                """,
                target_model,
            )
            sample = await db.fetchall(
                """
                SELECT id FROM memory_entry
                WHERE valid_until IS NULL AND embedding_model != $1
                ORDER BY embedded_at ASC LIMIT 10
                """,
                target_model,
            )
            return {
                "target_model": target_model,
                "total_stale": int(count_row["c"]) if count_row else 0,
                "sample_ids": [str(r["id"]) for r in sample],
                "re_embedded": 0,
                "errors": 0,
                "dry_run": True,
            }

        total_stale = 0
        re_embedded = 0
        errors = 0
        while True:
            rows = await db.fetchall(
                """
                SELECT id, content FROM memory_entry
                WHERE valid_until IS NULL AND embedding_model != $1
                ORDER BY embedded_at ASC LIMIT $2
                """,
                target_model,
                batch_size,
            )
            if not rows:
                break
            total_stale += len(rows)
            progressed = re_embedded
            for row in rows:
                try:
                    result = await embedder.embed(row["content"])
                    await db.execute(
                        """
                        UPDATE memory_entry
                        SET embedding = $1::vector, embedding_model = $2,
                            embedding_dimensions = $3, embedded_at = $4
                        WHERE id = $5
                        """,
                        json.dumps(result.vector),
                        result.model_id,
                        result.dimensions,
                        result.embedded_at,
                        row["id"],
                    )
                    re_embedded += 1
                    if re_embedded % 10 == 0:
                        print(f"re-embedding {re_embedded}...", file=sys.stderr)
                except Exception as exc:  # noqa: BLE001 — never abort the whole run
                    errors += 1
                    print(f"error re-embedding {row['id']}: {exc}", file=sys.stderr)
            # If a full page yielded no successful update, stop to avoid an
            # infinite loop over records that keep failing.
            if re_embedded == progressed:
                break

        return {
            "target_model": target_model,
            "total_stale": total_stale,
            "re_embedded": re_embedded,
            "errors": errors,
            "dry_run": False,
        }
    finally:
        await db.close()


async def _run_decay_report(
    settings: Settings, days: int, min_stored_trust: float
) -> list[dict]:
    """List memories whose stored trust_score has decayed >20% over time."""
    from datetime import UTC, datetime

    from .core.trust_score import TrustAdjustment

    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        rows = await db.fetchall(
            """
            SELECT id, content, trust_score, memory_type, created_at
            FROM memory_entry
            WHERE valid_until IS NULL
              AND trust_score >= $1
              AND created_at < now() - make_interval(days => $2)
            ORDER BY created_at ASC
            """,
            min_stored_trust,
            days,
        )
        now = datetime.now(UTC)
        report = []
        for row in rows:
            stored = float(row["trust_score"])
            age_days = (now - row["created_at"]).days
            effective = TrustAdjustment.decay_over_time(stored, age_days)
            if effective < stored * 0.8:
                report.append(
                    {
                        "id": str(row["id"]),
                        "memory_type": row["memory_type"],
                        "stored_trust": round(stored, 3),
                        "effective_trust": round(effective, 3),
                        "age_days": age_days,
                        "content": row["content"][:60],
                    }
                )
        report.sort(key=lambda r: r["stored_trust"] - r["effective_trust"], reverse=True)
        return report
    finally:
        await db.close()


async def _run_state_op(settings: Settings, args) -> dict:
    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        from .embedding.provider import EmbeddingProvider

        embedder = EmbeddingProvider.from_settings(settings) if args.command == "revise" else None
        tools = MemoryTools(
            db=db, embedder=embedder, chain_key=settings.chain_key, key_id=settings.chain_key_id
        )
        state = StateTools(
            db=db, memory_tools=tools, chain_key=settings.chain_key, key_id=settings.chain_key_id
        )
        if args.command == "invalidate":
            return await state.invalidate(args.memory_id, args.reason, args.actor)
        if args.command == "redact":
            return await state.redact(args.memory_id, args.reason, args.actor)
        return await state.revise(args.memory_id, args.content, args.reason, args.actor)
    finally:
        await db.close()


async def _run_daemon_start(settings: Settings) -> None:
    from .daemons.runner import DaemonRunner
    from .embedding.provider import EmbeddingProvider
    from .reranker.provider import RerankerProvider

    db = AsyncPostgresPool(
        db_url=settings.db_url,
        min_size=settings.db_min_size,
        max_size=settings.db_max_size,
    )
    await db.connect()
    embedder = EmbeddingProvider.from_settings(settings)
    reranker = RerankerProvider.from_settings(settings)
    tools = MemoryTools(
        db=db, embedder=embedder, chain_key=settings.chain_key,
        key_id=settings.chain_key_id, reranker=reranker,
    )
    runner = DaemonRunner(db=db, embedder=embedder, memory_tools=tools, settings=settings)
    print(f"starting daemons (inbox_workers={settings.inbox_worker_concurrency}, "
          f"conflict_resolvers={settings.conflict_resolver_concurrency})")
    await runner.run_forever()


async def _run_daemon_once(settings: Settings, which: str) -> dict:
    from .daemons.runner import DaemonRunner
    from .embedding.provider import EmbeddingProvider
    from .reranker.provider import RerankerProvider

    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=4)
    await db.connect()
    try:
        embedder = EmbeddingProvider.from_settings(settings)
        reranker = RerankerProvider.from_settings(settings)
        tools = MemoryTools(
            db=db, embedder=embedder, chain_key=settings.chain_key,
            key_id=settings.chain_key_id, reranker=reranker,
        )
        runner = DaemonRunner(db=db, embedder=embedder, memory_tools=tools, settings=settings)
        if which == "insights":
            return await runner.run_insights_once()
        return await runner.run_maintenance_once()
    finally:
        await db.close()


async def _run_inbox_status(settings: Settings) -> dict:
    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        rows = await db.fetchall(
            """
            SELECT status, COUNT(*) AS cnt
            FROM memory_inbox
            GROUP BY status
            ORDER BY status
            """
        )
        return {r["status"]: r["cnt"] for r in rows}
    finally:
        await db.close()


async def _run_inbox_review(settings: Settings, limit: int) -> list:
    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        rows = await db.fetchall(
            """
            SELECT id, content, source_agent, submitted_at, review_reason,
                   caller_type, caller_trust, retry_count, error
            FROM memory_inbox
            WHERE status = 'held'
            ORDER BY submitted_at ASC
            LIMIT $1
            """,
            limit,
        )
        return [
            {
                "id": str(r["id"]),
                "content": r["content"][:120],
                "source_agent": r["source_agent"],
                "submitted_at": r["submitted_at"].isoformat(),
                "review_reason": r["review_reason"],
                "caller_type": r["caller_type"],
                "caller_trust": float(r["caller_trust"]),
                "retry_count": r["retry_count"],
                "error": r["error"],
            }
            for r in rows
        ]
    finally:
        await db.close()


async def _run_inbox_approve(settings: Settings, inbox_id: str, actor: str) -> dict:
    """Approve a held inbox item: write directly to memory chain, bypass classifier."""
    from .embedding.provider import EmbeddingProvider

    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        row = await db.fetchrow(
            """
            SELECT id, content, source_agent, session_id, caller_type, caller_trust
            FROM memory_inbox
            WHERE id = $1 AND status = 'held'
            """,
            inbox_id,
        )
        if row is None:
            raise ValueError(f"inbox item {inbox_id} not found or not in 'held' status")

        embedder = EmbeddingProvider.from_settings(settings)
        tools = MemoryTools(
            db=db, embedder=embedder, chain_key=settings.chain_key, key_id=settings.chain_key_id
        )
        result = await tools.capture_memory(
            content=row["content"],
            memory_type=row["caller_type"],
            trust_score=float(row["caller_trust"]),
            actor=actor,
            source_agent=row["source_agent"],
            session_id=row["session_id"],
            metadata={"inbox_id": inbox_id, "approved_by": actor},
        )
        await db.execute(
            """
            UPDATE memory_inbox
            SET status = 'approved', promoted_to = $1, processed_at = now()
            WHERE id = $2
            """,
            result["id"],
            inbox_id,
        )
        return {"approved": inbox_id, "promoted_to": result["id"]}
    finally:
        await db.close()


async def _run_inbox_reject(settings: Settings, inbox_id: str, reason: str) -> dict:
    """Reject a held inbox item permanently."""
    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        result = await db.execute(
            """
            UPDATE memory_inbox
            SET status = 'rejected', rejection_reason = $1, processed_at = now()
            WHERE id = $2 AND status = 'held'
            """,
            reason,
            inbox_id,
        )
        if result == "UPDATE 0":
            raise ValueError(f"inbox item {inbox_id} not found or not in 'held' status")
        return {"rejected": inbox_id, "reason": reason}
    finally:
        await db.close()


async def _run_inbox_retry(settings: Settings, inbox_id: str) -> dict:
    """Push a held inbox item back to pending for reprocessing."""
    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        result = await db.execute(
            """
            UPDATE memory_inbox
            SET status = 'pending', error = NULL, worker_id = NULL,
                retry_count = 0, processed_at = NULL
            WHERE id = $1 AND status = 'held'
            """,
            inbox_id,
        )
        if result == "UPDATE 0":
            raise ValueError(f"inbox item {inbox_id} not found or not in 'held' status")
        return {"retrying": inbox_id}
    finally:
        await db.close()


async def _run_constitutional(settings: Settings, args) -> Any:
    from .constitutional.manager import ConstitutionalError, ConstitutionalManager

    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        mgr = ConstitutionalManager()
        if args.constitutional_cmd == "add":
            try:
                parameters = json.loads(args.parameters)
            except json.JSONDecodeError as exc:
                raise ConstitutionalError(f"--parameters is not valid JSON: {exc}") from exc
            return await mgr.add_rule(
                db,
                chain_key=settings.chain_key,
                key_id=settings.chain_key_id,
                rule_type=args.rule_type,
                parameters=parameters,
                description=args.description,
                applies_to=args.applies_to,
            )
        if args.constitutional_cmd == "list":
            rules = await mgr.list_rules(db)
            return [
                {
                    "id": r.id,
                    "rule_type": r.rule_type,
                    "parameters": r.parameters,
                    "description": r.description,
                    "applies_to": r.applies_to,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rules
            ]
        if args.constitutional_cmd == "revoke":
            return await mgr.revoke_rule(db, args.rule_id)
        # verify — all rules ever signed, revoked included: a tampered retired
        # rule is just as much an integrity breach as a tampered active one
        rules = await mgr.load_all_rules(db)
        tampered = [
            r.id for r in rules if not await mgr.verify_rule(r, settings.chain_key)
        ]
        return {
            "rules_checked": len(rules),
            "revoked_checked": sum(1 for r in rules if r.revoked_at is not None),
            "tampered": tampered,
            "all_valid": not tampered,
        }
    finally:
        await db.close()


async def _run_judicial_precedents(settings: Settings) -> list:
    from .judicial.precedent import PrecedentStore

    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        precedents = await PrecedentStore().list_precedents(db)
        return [
            {
                "id": p.id,
                "contradiction_type": p.contradiction_type,
                "resolution": p.resolution,
                "winner_rule": p.winner_rule,
                "confidence": round(p.confidence, 3),
                "applied_count": p.applied_count,
            }
            for p in precedents
        ]
    finally:
        await db.close()


async def _run_judicial_pending(settings: Settings) -> list:
    from .judicial.escalation import HumanEscalationQueue

    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        return await HumanEscalationQueue().list_pending(db)
    finally:
        await db.close()


async def _run_judicial_resolve(
    settings: Settings, entry_id: str, resolution: str, actor: str
) -> dict:
    from .judicial.escalation import HumanEscalationQueue

    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        await HumanEscalationQueue().resolve(db, entry_id, resolution, actor)
        return {"resolved": entry_id, "resolution": resolution, "resolved_by": actor}
    finally:
        await db.close()


async def _run_graph(settings: Settings, args) -> Any:
    from .graph import GraphStore

    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        store = GraphStore()
        if args.graph_cmd == "entities":
            rows = await db.fetchall(
                """
                SELECT name, entity_type, created_at
                FROM entity
                WHERE ($1::text IS NULL OR entity_type = $1)
                ORDER BY entity_type, name
                """,
                args.type,
            )
            return [
                {
                    "name": r["name"],
                    "entity_type": r["entity_type"],
                    "created_at": r["created_at"].isoformat(),
                }
                for r in rows
            ]
        if args.graph_cmd == "search":
            return await store.search_by_entity(db, args.entity, limit=args.limit)
        # relations
        return await store.get_entity_graph(db, args.entity)
    finally:
        await db.close()


async def _run_export(settings: Settings, args) -> dict:
    import sys as _sys
    from contextlib import nullcontext

    from .portability import MemoryExporter

    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        exporter = MemoryExporter(db)
        # Stream to the chosen sink; stdout stays open (nullcontext) so we
        # never close the interpreter's own stream.
        out_ctx = (
            open(args.output, "w", encoding="utf-8") if args.output else nullcontext(_sys.stdout)
        )
        with out_ctx as out:
            return await exporter.export(
                out,
                include_audit=args.include_audit,
                include_redacted=args.include_redacted,
                memory_type=args.memory_type,
                min_trust=args.min_trust,
            )
    finally:
        await db.close()


async def _run_import(settings: Settings, args) -> dict:
    from .embedding.provider import EmbeddingProvider
    from .portability import MemoryImporter

    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=2)
    await db.connect()
    try:
        embedder = EmbeddingProvider.from_settings(settings)
        importer = MemoryImporter(
            db=db,
            embedder=embedder,
            chain_key=settings.chain_key,
            key_id=settings.chain_key_id,
            dry_run=args.dry_run,
            trust_ceiling=args.trust_ceiling,
        )
        with open(args.path, encoding="utf-8") as inp:
            return await importer.import_stream(inp)
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jeli", description="Jeli sovereign memory — governance CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── verify ────────────────────────────────────────────────────────────────
    verify_p = sub.add_parser("verify", help="verify hash-chain integrity")
    verify_p.add_argument("--json", action="store_true", help="machine-readable output")
    verify_p.add_argument(
        "--report", action="store_true", help="full integrity health report (JSON)"
    )

    # ── re-embed ──────────────────────────────────────────────────────────────
    reembed_p = sub.add_parser(
        "re-embed", help="re-embed memories with a stale embedding model"
    )
    reembed_p.add_argument("--dry-run", dest="dry_run", action="store_true")
    reembed_p.add_argument("--batch-size", dest="batch_size", type=int, default=50)
    reembed_p.add_argument(
        "--model", default=None, help="target model id (default: current embedder)"
    )

    # ── decay-report ──────────────────────────────────────────────────────────
    decay_p = sub.add_parser(
        "decay-report", help="show memories whose stored trust has decayed >20%"
    )
    decay_p.add_argument("--days", type=int, default=90)
    decay_p.add_argument(
        "--min-stored-trust", dest="min_stored_trust", type=float, default=0.7
    )
    decay_p.add_argument("--json", action="store_true", help="machine-readable output")

    # ── invalidate / revise ───────────────────────────────────────────────────
    inv_p = sub.add_parser("invalidate", help="retire a memory (chained event; never deletes)")
    inv_p.add_argument("memory_id")
    inv_p.add_argument("--reason", required=True)
    inv_p.add_argument("--actor", default="jp")

    rev_p = sub.add_parser("revise", help="append a corrected successor and supersede original")
    rev_p.add_argument("memory_id")
    rev_p.add_argument("--content", required=True)
    rev_p.add_argument("--reason", required=True)
    rev_p.add_argument("--actor", default="jp")

    red_p = sub.add_parser(
        "redact",
        help="redact a memory (chained event; content masked at read time, row never rewritten)",
    )
    red_p.add_argument("memory_id")
    red_p.add_argument("--reason", required=True)
    red_p.add_argument("--actor", default="jp")

    # ── daemon ────────────────────────────────────────────────────────────────
    daemon_p = sub.add_parser("daemon", help="manage background daemons")
    daemon_sub = daemon_p.add_subparsers(dest="daemon_cmd", required=True)
    daemon_sub.add_parser("start", help="start inbox worker + conflict resolver (blocking)")
    daemon_sub.add_parser("insights", help="run insights daemon once")
    daemon_sub.add_parser("maintenance", help="run maintenance daemon once")

    # ── inbox ─────────────────────────────────────────────────────────────────
    inbox_p = sub.add_parser("inbox", help="inspect the memory inbox")
    inbox_sub = inbox_p.add_subparsers(dest="inbox_cmd", required=True)
    inbox_sub.add_parser("status", help="show counts by status")
    review_p = inbox_sub.add_parser("review", help="list held items for human review")
    review_p.add_argument("--limit", type=int, default=20)
    approve_p = inbox_sub.add_parser("approve", help="approve a held item (writes to chain)")
    approve_p.add_argument("inbox_id")
    approve_p.add_argument("--actor", default="jp")
    reject_p = inbox_sub.add_parser("reject", help="permanently reject a held item")
    reject_p.add_argument("inbox_id")
    reject_p.add_argument("--reason", required=True)
    retry_p = inbox_sub.add_parser("retry", help="push a held item back to pending")
    retry_p.add_argument("inbox_id")

    # ── constitutional ─────────────────────────────────────────────────────────
    const_p = sub.add_parser(
        "constitutional",
        help="manage user-signed constitutional rules (Read + Write gates)",
    )
    const_sub = const_p.add_subparsers(dest="constitutional_cmd", required=True)
    add_p = const_sub.add_parser(
        "add",
        help="add and sign a new rule",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  jeli constitutional add --rule-type deny_write_memory_type \\\n"
            "    --parameters '{\"memory_type\":\"identity\"}' \\\n"
            '    --description "Agents cannot write identity memories" \\\n'
            '    --applies-to "hermes"\n'
            "\n"
            "  jeli constitutional add --rule-type max_trust_for_content_class \\\n"
            "    --parameters '{\"content_class\":\"external\",\"max_trust\":0.3}' \\\n"
            '    --description "External content capped at 0.3 trust"\n'
        ),
    )
    add_p.add_argument(
        "--rule-type",
        dest="rule_type",
        required=True,
        choices=[
            "exclude_memory_type",
            "min_trust_floor",
            "exclude_tag",
            "exclude_content_class",
            "max_results",
            "deny_write_memory_type",
            "max_trust_for_content_class",
        ],
    )
    add_p.add_argument("--parameters", required=True, help="JSON object of rule params")
    add_p.add_argument("--description", required=True, help="human-readable statement of intent")
    add_p.add_argument("--applies-to", dest="applies_to", default="all")
    const_sub.add_parser("list", help="list active rules")
    revoke_p = const_sub.add_parser("revoke", help="retire a rule (never deletes)")
    revoke_p.add_argument("--rule-id", dest="rule_id", required=True)
    const_sub.add_parser("verify", help="recompute rule hashes, report any tampered")

    # ── judicial ────────────────────────────────────────────────────────────────
    jud_p = sub.add_parser("judicial", help="inspect Judicial case law and escalations")
    jud_sub = jud_p.add_subparsers(dest="judicial_cmd", required=True)
    jud_sub.add_parser("precedents", help="list settled precedents")
    jud_sub.add_parser("pending", help="list conflicts awaiting user resolution")
    jud_resolve_p = jud_sub.add_parser("resolve", help="resolve a human-queue entry")
    jud_resolve_p.add_argument("--entry-id", dest="entry_id", required=True)
    jud_resolve_p.add_argument("--resolution", required=True)
    jud_resolve_p.add_argument("--actor", default="jp")

    # ── graph ─────────────────────────────────────────────────────────────────
    graph_p = sub.add_parser("graph", help="inspect the entity graph")
    graph_sub = graph_p.add_subparsers(dest="graph_cmd", required=True)
    entities_p = graph_sub.add_parser("entities", help="list known entities")
    entities_p.add_argument(
        "--type",
        choices=["person", "project", "organization", "technology", "concept", "location"],
        default=None,
        help="filter by entity type",
    )
    graph_search_p = graph_sub.add_parser("search", help="memories mentioning an entity")
    graph_search_p.add_argument("--entity", required=True)
    graph_search_p.add_argument("--limit", type=int, default=10)
    graph_rel_p = graph_sub.add_parser("relations", help="an entity's relations + memory count")
    graph_rel_p.add_argument("--entity", required=True)

    # ── export / import ─────────────────────────────────────────────────────────
    export_p = sub.add_parser("export", help="stream the memory store to a JSON-Lines archive")
    export_p.add_argument("--output", default=None, help="output file (default: stdout)")
    export_p.add_argument("--include-audit", dest="include_audit", action="store_true")
    export_p.add_argument("--include-redacted", dest="include_redacted", action="store_true")
    export_p.add_argument("--memory-type", dest="memory_type", default=None)
    export_p.add_argument("--min-trust", dest="min_trust", type=float, default=None)

    import_p = sub.add_parser("import", help="import a JSON-Lines archive into the local store")
    import_p.add_argument("path")
    import_p.add_argument("--dry-run", dest="dry_run", action="store_true")
    import_p.add_argument(
        "--trust-ceiling",
        dest="trust_ceiling",
        type=float,
        default=DEFAULT_IMPORT_TRUST_CEILING,
        help="cap imported trust (default 0.3; raise only for a known-good "
        "local restore of your own export)",
    )

    args = parser.parse_args(argv)
    settings = Settings()
    if not settings.chain_key:
        print("error: SCOPED_MCP_CHAIN_KEY is not set", file=sys.stderr)
        return 2

    if args.command == "verify":
        if args.report:
            report = asyncio.run(_run_integrity_report(settings))
            print(json.dumps(report, indent=2))
            ok = (
                report["chain_valid"]
                and report.get("state_chain_valid", True)
                and report.get("cache_consistent") is not False
            )
            return 0 if ok else 1
        result = asyncio.run(_run_verify(settings))
        if args.json:
            print(json.dumps(result))
        elif (
            result["chain_valid"]
            and result.get("state_chain_valid", True)
            and result.get("cache_consistent") is not False
        ):
            print(
                f"✓ chains valid — {result['records_checked']} records, "
                f"{result.get('events_checked', 0)} state events, cache consistent"
            )
        else:
            print(
                f"✗ CHAIN BROKEN — first tampered record: "
                f"{result['first_bad_record']} "
                f"({result['records_checked']} records walked)"
            )
        ok = (
            result["chain_valid"]
            and result.get("state_chain_valid", True)
            and result.get("cache_consistent") is not False
        )
        return 0 if ok else 1

    if args.command == "re-embed":
        result = asyncio.run(
            _run_re_embed(settings, args.dry_run, args.batch_size, args.model)
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "decay-report":
        decayed = asyncio.run(
            _run_decay_report(settings, args.days, args.min_stored_trust)
        )
        if args.json:
            print(json.dumps(decayed, indent=2))
            return 0
        if not decayed:
            print("no memories have decayed >20% from stored trust")
            return 0
        print(
            f"{'ID (short)':<14}{'Type':<12}{'Stored':>7}{'Effective':>11}"
            f"{'Age(days)':>11}  Content snippet"
        )
        print(f"{'-' * 12:<14}{'-' * 10:<12}{'-' * 6:>7}{'-' * 9:>11}{'-' * 9:>11}  {'-' * 19}")
        for r in decayed:
            print(
                f"{r['id'][:12]:<14}{r['memory_type']:<12}"
                f"{r['stored_trust']:>7.2f}{r['effective_trust']:>11.2f}"
                f"{r['age_days']:>11}  {r['content']!r}"
            )
        print(
            f"\n{len(decayed)} memories have decayed >20% from stored trust. "
            "Consider running `jeli revise` on important ones."
        )
        return 0

    if args.command in ("invalidate", "revise", "redact"):
        result = asyncio.run(_run_state_op(settings, args))
        print(json.dumps(result, indent=1))
        return 0

    if args.command == "daemon":
        if args.daemon_cmd == "start":
            asyncio.run(_run_daemon_start(settings))
            return 0
        result = asyncio.run(_run_daemon_once(settings, args.daemon_cmd))
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "inbox":
        if args.inbox_cmd == "status":
            result = asyncio.run(_run_inbox_status(settings))
            for status, count in result.items():
                print(f"  {status:12s} {count}")
            return 0
        if args.inbox_cmd == "review":
            items = asyncio.run(_run_inbox_review(settings, args.limit))
            if not items:
                print("no held items")
                return 0
            print(json.dumps(items, indent=2))
            return 0
        if args.inbox_cmd == "approve":
            try:
                result = asyncio.run(_run_inbox_approve(settings, args.inbox_id, args.actor))
                print(json.dumps(result, indent=2))
                return 0
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        if args.inbox_cmd == "reject":
            try:
                result = asyncio.run(_run_inbox_reject(settings, args.inbox_id, args.reason))
                print(json.dumps(result, indent=2))
                return 0
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        if args.inbox_cmd == "retry":
            try:
                result = asyncio.run(_run_inbox_retry(settings, args.inbox_id))
                print(json.dumps(result, indent=2))
                return 0
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1

    if args.command == "constitutional":
        from .constitutional.manager import ConstitutionalError

        try:
            result = asyncio.run(_run_constitutional(settings, args))
        except ConstitutionalError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.constitutional_cmd == "verify":
            if result["all_valid"]:
                print(f"✓ {result['rules_checked']} constitutional rules valid")
            else:
                print(f"✗ TAMPERED rules: {', '.join(result['tampered'])}")
            return 0 if result["all_valid"] else 1
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "judicial":
        if args.judicial_cmd == "precedents":
            items = asyncio.run(_run_judicial_precedents(settings))
            if not items:
                print("no precedents recorded")
                return 0
            print(json.dumps(items, indent=2))
            return 0
        if args.judicial_cmd == "pending":
            items = asyncio.run(_run_judicial_pending(settings))
            if not items:
                print("no pending escalations")
                return 0
            print(json.dumps(items, indent=2))
            return 0
        if args.judicial_cmd == "resolve":
            try:
                result = asyncio.run(
                    _run_judicial_resolve(
                        settings, args.entry_id, args.resolution, args.actor
                    )
                )
                print(json.dumps(result, indent=2))
                return 0
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1

    if args.command == "graph":
        result = asyncio.run(_run_graph(settings, args))
        if not result:
            print("no results")
            return 0
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "export":
        summary = asyncio.run(_run_export(settings, args))
        # Summary goes to stderr so it never contaminates a stdout archive.
        print(json.dumps(summary, indent=2), file=sys.stderr)
        return 0

    if args.command == "import":
        from .portability.importer import ImportError as ArchiveImportError

        try:
            summary = asyncio.run(_run_import(settings, args))
        except (ArchiveImportError, FileNotFoundError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
