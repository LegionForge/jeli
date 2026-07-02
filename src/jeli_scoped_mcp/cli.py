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


async def _run_verify(settings: Settings) -> dict:
    db = AsyncPostgresPool(
        db_url=settings.db_url,
        min_size=1,
        max_size=2,
    )
    await db.connect()
    try:
        tools = MemoryTools(db=db, embedder=None, chain_key=settings.chain_key)
        return await tools.verify_chain()
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
    args = parser.parse_args(argv)

    if args.command == "verify":
        settings = Settings()
        if not settings.chain_key:
            print("error: SCOPED_MCP_CHAIN_KEY is not set", file=sys.stderr)
            return 2
        result = asyncio.run(_run_verify(settings))
        if args.json:
            print(json.dumps(result))
        elif result["chain_valid"]:
            print(f"✓ chain valid — {result['records_checked']} records verified")
        else:
            print(
                f"✗ CHAIN BROKEN — first tampered record: "
                f"{result['first_bad_record']} "
                f"({result['records_checked']} records walked)"
            )
        return 0 if result["chain_valid"] else 1

    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    sys.exit(main())
