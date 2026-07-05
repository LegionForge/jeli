"""jeli command-line interface."""

import argparse
import asyncio
import json
import sys

from .config import Settings
from .database.pool import AsyncPostgresPool
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jeli", description="Jeli sovereign memory — governance CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── verify ────────────────────────────────────────────────────────────────
    verify_p = sub.add_parser("verify", help="verify hash-chain integrity")
    verify_p.add_argument("--json", action="store_true", help="machine-readable output")

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

    args = parser.parse_args(argv)
    settings = Settings()
    if not settings.chain_key:
        print("error: SCOPED_MCP_CHAIN_KEY is not set", file=sys.stderr)
        return 2

    if args.command == "verify":
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

    return 2


if __name__ == "__main__":
    sys.exit(main())
