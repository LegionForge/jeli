"""Benchmark Jeli search + verification at current store size.

  JELI_CHAIN_KEY=... python scripts/bench_search.py --db-url URL [--queries N]

Reports p50/p95 latency for semantic and fts search and the full
verify_chain runtime — the numbers behind the Phase-1 "<100ms at 10k"
success criterion.
"""

import argparse
import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jeli_scoped_mcp.database.pool import AsyncPostgresPool  # noqa: E402
from jeli_scoped_mcp.embedding.provider import OllamaProvider  # noqa: E402
from jeli_scoped_mcp.tools.memory_tools import MemoryTools  # noqa: E402

QUERIES = [
    "how do we handle secrets and API keys",
    "what embedding model should we use",
    "postgres configuration and ports",
    "docker container hardening",
    "what happened with the hermes bot",
    "lessons about github actions and CI",
    "how does the hash chain work",
    "obsidian vault conventions",
    "llm-valet release testing",
    "network addresses and tailscale",
]


def pctl(xs, p):
    xs = sorted(xs)
    return xs[min(int(len(xs) * p), len(xs) - 1)]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--queries", type=int, default=50)
    args = ap.parse_args()

    db = AsyncPostgresPool(args.db_url, 1, 4)
    await db.connect()
    tools = MemoryTools(
        db=db,
        embedder=OllamaProvider(model="snowflake-arctic-embed2:latest"),
        chain_key=os.environ["JELI_CHAIN_KEY"],
    )
    n = await db.fetchval("SELECT count(*) FROM memory_entry")
    print(f"store size: {n} memories")

    for mode in ("semantic", "fts"):
        lat = []
        for i in range(args.queries):
            q = QUERIES[i % len(QUERIES)]
            t0 = time.monotonic()
            if mode == "semantic":
                # split embed vs db time: embed once, time the tool call whole
                hits = await tools.search_memory(query=q, actor="bench", mode=mode, limit=10)
            else:
                hits = await tools.search_memory(
                    query=q.split()[0], actor="bench", mode=mode, limit=10
                )
            lat.append((time.monotonic() - t0) * 1000)
        print(
            f"{mode:9s} p50={pctl(lat, 0.5):7.1f}ms  p95={pctl(lat, 0.95):7.1f}ms  "
            f"mean={statistics.mean(lat):7.1f}ms  (n={args.queries}, incl. query embedding for semantic)"
        )

    t0 = time.monotonic()
    v = await tools.verify_chain()
    print(
        f"verify_chain: {(time.monotonic() - t0):.1f}s for {v['records_checked']} records "
        f"(valid={v['chain_valid']})"
    )

    # top-3 sanity for one query
    hits = await tools.search_memory(
        query="what embedding model should we use", actor="bench", mode="semantic", limit=3
    )
    print("\nsample semantic results for 'what embedding model should we use':")
    for h in hits:
        print(f"  d={h['distance']:.3f} [{h['memory_type']}] {h['content'][:70]!r}")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
