"""jeli command-line interface.

`jeli verify` — walk the full memory chain, recompute every HMAC, and report
the first tampered record. This is the user-facing trust artifact: it needs
the DB and the chain key, but no API key and no embedding provider.
"""

import argparse
import asyncio
import json
import sys

from .config import Settings
from .database.pool import AsyncPostgresPool
from .tools.memory_tools import MemoryTools
from .tools.state_tools import StateTools


async def _run_verify(settings: Settings) -> dict:
    db = AsyncPostgresPool(
        db_url=settings.db_url,
        min_size=1,
        max_size=2,
    )
    await db.connect()
    try:
        tools = MemoryTools(
            db=db,
            embedder=None,
            chain_key=settings.chain_key,
            key_id=settings.chain_key_id,
        )
        result = await tools.verify_chain()
        state = StateTools(
            db=db,
            memory_tools=tools,
            chain_key=settings.chain_key,
            key_id=settings.chain_key_id,
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
            db=db,
            embedder=embedder,
            chain_key=settings.chain_key,
            key_id=settings.chain_key_id,
        )
        state = StateTools(
            db=db,
            memory_tools=tools,
            chain_key=settings.chain_key,
            key_id=settings.chain_key_id,
        )
        if args.command == "invalidate":
            return await state.invalidate(args.memory_id, args.reason, args.actor)
        return await state.revise(args.memory_id, args.content, args.reason, args.actor)
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jeli", description="Jeli sovereign memory — governance CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    verify_p = sub.add_parser(
        "verify", help="verify hash-chain integrity of the whole memory store"
    )
    verify_p.add_argument("--json", action="store_true", help="machine-readable output")

    inv_p = sub.add_parser("invalidate", help="retire a memory (chained event; never deletes)")
    inv_p.add_argument("memory_id")
    inv_p.add_argument("--reason", required=True)
    inv_p.add_argument("--actor", default="jp")

    rev_p = sub.add_parser("revise", help="append a corrected successor and supersede the original")
    rev_p.add_argument("memory_id")
    rev_p.add_argument("--content", required=True, help="the corrected memory text")
    rev_p.add_argument("--reason", required=True)
    rev_p.add_argument("--actor", default="jp")

    args = parser.parse_args(argv)

    if args.command == "verify":
        settings = Settings()
        if not settings.chain_key:
            print("error: SCOPED_MCP_CHAIN_KEY is not set", file=sys.stderr)
            return 2
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

    if args.command in ("invalidate", "revise"):
        settings = Settings()
        if not settings.chain_key:
            print("error: SCOPED_MCP_CHAIN_KEY is not set", file=sys.stderr)
            return 2
        result = asyncio.run(_run_state_op(settings, args))
        print(json.dumps(result, indent=1))
        return 0

    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    sys.exit(main())
