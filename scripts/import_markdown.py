"""Bulk-import a directory of markdown notes into Jeli.

Generic and config-driven: no personal paths or vault structure baked in.
Every record goes through MemoryTools.capture_memory, so imports are
hash-chained, audited, dimension-guarded, and injection-screened exactly
like live agent writes — an importer that bypassed the scoped write path
would be a second, unguarded door into the store.

Usage:
  python scripts/import_markdown.py --source DIR --db-url URL [options]

Options:
  --source DIR          root directory of .md files (recursive)
  --db-url URL          postgres URL (use a STAGING database first)
  --actor NAME          provenance actor              [importer]
  --trust FLOAT         trust score for the corpus    [0.9 user-confirmed]
  --type-map JSON       path-substring -> memory_type, first match wins,
                        e.g. '{"lessons":"procedural","Sessions":"episodic"}'
  --default-type TYPE   fallback memory_type          [semantic]
  --exclude SUBSTR      path substrings to skip (repeatable)
  --min-chars N         skip chunks shorter than N    [80]
  --max-files N         cap for trial runs
  --report PATH         write a markdown report here
  --dry-run             parse/chunk/classify only; no writes

Chunking: one memory per markdown section (## heading), falling back to the
whole file when it has no sections. Headings are kept as context prefix.
Chain key comes from JELI_CHAIN_KEY (env), never argv.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jeli_scoped_mcp.database.pool import AsyncPostgresPool  # noqa: E402
from jeli_scoped_mcp.embedding.provider import OllamaProvider  # noqa: E402
from jeli_scoped_mcp.tools.memory_tools import (  # noqa: E402
    MemoryToolError,
    MemoryTools,
)

FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)


def chunk_file(path: Path, min_chars: int) -> list[tuple[str, str]]:
    """Return (title, text) chunks: one per ## section, else whole file."""
    text = path.read_text(errors="replace")
    text = FRONTMATTER_RE.sub("", text)
    parts = SECTION_RE.split(text)
    chunks = []
    if len(parts) > 1:
        preamble, sections = parts[0], parts[1:]
        if len(preamble.strip()) >= min_chars:
            chunks.append((path.stem, preamble.strip()))
        for sec in sections:
            title = sec.splitlines()[0].strip() if sec.splitlines() else path.stem
            body = sec.strip()
            if len(body) >= min_chars:
                chunks.append((f"{path.stem} › {title}", "## " + body))
    else:
        if len(text.strip()) >= min_chars:
            chunks.append((path.stem, text.strip()))
    return chunks


def classify(path: str, type_map: dict, default: str) -> str:
    for needle, mtype in type_map.items():
        if needle.lower() in path.lower():
            return mtype
    return default


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--actor", default="importer")
    ap.add_argument("--trust", type=float, default=0.9)
    ap.add_argument("--type-map", default="{}")
    ap.add_argument("--default-type", default="semantic")
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--min-chars", type=int, default=80)
    ap.add_argument("--max-files", type=int, default=0)
    ap.add_argument("--report", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    type_map = json.loads(args.type_map)
    chain_key = os.getenv("JELI_CHAIN_KEY", "")
    if not chain_key and not args.dry_run:
        print("JELI_CHAIN_KEY not set", file=sys.stderr)
        return 2

    files = sorted(
        p
        for p in Path(args.source).rglob("*.md")
        if not any(x.lower() in str(p).lower() for x in args.exclude)
    )
    if args.max_files:
        files = files[: args.max_files]

    stats: Counter = Counter()
    by_type: Counter = Counter()
    flagged_titles: list[str] = []
    errors: list[str] = []
    seen_hashes: set[str] = set()
    t0 = time.monotonic()

    db = tools = None
    if not args.dry_run:
        db = AsyncPostgresPool(args.db_url, 1, 4)
        await db.connect()
        tools = MemoryTools(
            db=db,
            embedder=OllamaProvider(model="snowflake-arctic-embed2:latest"),
            chain_key=chain_key,
        )
        # dedupe against what's already in the store (idempotent re-runs)
        rows = await db.fetchall("SELECT content_hash FROM memory_entry")
        seen_hashes = {r["content_hash"] for r in rows}
        stats["preexisting"] = len(seen_hashes)

    for f in files:
        stats["files"] += 1
        rel = str(f.relative_to(args.source))
        mtype = classify(rel, type_map, args.default_type)
        try:
            chunks = chunk_file(f, args.min_chars)
        except Exception as e:
            errors.append(f"{rel}: {e}")
            continue
        for title, body in chunks:
            stats["chunks"] += 1
            content = f"[{title}]\n{body}"
            h = hashlib.sha256(content.encode()).hexdigest()
            if h in seen_hashes:
                stats["deduped"] += 1
                continue
            seen_hashes.add(h)
            by_type[mtype] += 1
            if args.dry_run:
                continue
            try:
                r = await tools.capture_memory(
                    content=content,
                    memory_type=mtype,
                    trust_score=args.trust,
                    actor=args.actor,
                    source_agent=args.actor,
                    metadata={"source_path": rel},
                )
                stats["imported"] += 1
                if r["injection_flagged"]:
                    stats["flagged"] += 1
                    flagged_titles.append(title)
            except MemoryToolError as e:
                errors.append(f"{rel} [{title}]: {e}")
        if stats["files"] % 50 == 0:
            rate = stats["imported"] / max(time.monotonic() - t0, 1)
            print(
                f"  … {stats['files']} files, {stats['imported']} imported " f"({rate:.1f}/s)",
                flush=True,
            )

    elapsed = time.monotonic() - t0
    if db:
        verify = await tools.verify_chain()
        await db.close()
    else:
        verify = {"chain_valid": None, "records_checked": 0}

    lines = [
        "# Jeli import report",
        "",
        f"- source: `{args.source}` · actor `{args.actor}` · trust {args.trust}"
        f" · dry-run: {args.dry_run}",
        f"- files: {stats['files']} · chunks: {stats['chunks']}"
        f" · imported: {stats['imported']} · deduped: {stats['deduped']}"
        f" · pre-existing: {stats['preexisting']}",
        f"- elapsed: {elapsed:.0f}s" f" ({stats['imported'] / max(elapsed, 1):.1f} memories/s)",
        f"- chain after import: valid={verify['chain_valid']}"
        f" records={verify['records_checked']}",
        "",
        "## by memory_type",
        *[f"- {k}: {v}" for k, v in by_type.most_common()],
        "",
        f"## injection-flagged ({stats['flagged']}) — capped at trust 0.3, review",
        *[f"- {t}" for t in flagged_titles[:40]],
        "",
        f"## errors ({len(errors)})",
        *[f"- {e}" for e in errors[:40]],
    ]
    report = "\n".join(lines)
    if args.report:
        Path(args.report).write_text(report)
        print(f"report → {args.report}")
    print(report[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
