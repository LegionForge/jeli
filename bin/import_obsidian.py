#!/usr/bin/env python3
"""Import a selection of Obsidian vault notes into Jeli.

Usage (from project root, with Ollama running):
    python bin/import_obsidian.py [--dry-run] [--vault-root /path/to/vault]

The script reads a curated list of vault-relative paths, chunks each file at
paragraph boundaries, classifies each chunk's memory type heuristically, and
submits it to the Jeli inbox via capture_memory.

Run `jeli inbox status` after to see what was queued. Start the daemon (or
use `python bin/import_obsidian.py` with --process) to classify and write.
"""

import argparse
import asyncio
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jeli_scoped_mcp.config import Settings
from jeli_scoped_mcp.database.pool import AsyncPostgresPool
from jeli_scoped_mcp.embedding.provider import EmbeddingProvider
from jeli_scoped_mcp.inbox.classifier import IngestionClassifier
from jeli_scoped_mcp.inbox.worker import InboxWorker
from jeli_scoped_mcp.server.mcp_server import ScopedMCPServer
from jeli_scoped_mcp.tools.memory_tools import MemoryTools

# ── content class inference ───────────────────────────────────────────────────

_SECURITY_DOC_KEYWORDS = frozenset(
    "security threat attack defense exploit inject vulnerability cve pentest "
    "adversarial poison jailbreak bypass override lessons-security audit".split()
)
_CODE_SAMPLE_EXTENSIONS = frozenset(".py .js .ts .sh .sql .go .rs".split())


def _infer_content_class(rel_path: str) -> str:
    """Derive content_class from vault-relative path for two-axis trust logic."""
    p = rel_path.lower()
    if any(kw in p for kw in _SECURITY_DOC_KEYWORDS):
        return "security-doc"
    if any(p.endswith(ext) for ext in _CODE_SAMPLE_EXTENSIONS):
        return "code-sample"
    return "general"


# ── vault file manifest ───────────────────────────────────────────────────────
# Each entry: (vault-relative path, memory_type, trust_score)
# content_class is derived automatically by _infer_content_class().
# Adjust paths to match your vault structure.
MANIFEST = [
    # Identity & user profile — highest trust, near-permanent
    ("Library/AI/user-profile.md",               "identity",   1.0),

    # Active project quickref — procedural + semantic
    ("Library/AI/projects/jeli-quickref.md",      "procedural", 0.9),

    # Lessons — semantic knowledge extracted from experience
    ("Library/AI/memory/lessons/lessons-security.md",   "semantic", 0.9),
    ("Library/AI/memory/lessons/lessons-index.md",      "semantic", 0.9),

    # Recent session notes — episodic
    ("Library/AI/Sessions/session-2026-07-03.md", "episodic",   0.8),
    ("Library/AI/Sessions/session-2026-07-04.md", "episodic",   0.8),

    # Startup / checkpoint — procedural context
    ("Library/AI/memory/!startup.md",             "procedural", 0.8),
]

# ── chunking ──────────────────────────────────────────────────────────────────

MIN_CHUNK = 80    # chars — skip very short paragraphs
MAX_CHUNK = 1200  # chars — split longer paragraphs at sentence boundary

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _split_long(text: str) -> list[str]:
    """Split a paragraph that exceeds MAX_CHUNK at sentence boundaries."""
    sentences = _SENTENCE_END.split(text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) + 1 > MAX_CHUNK and current:
            chunks.append(current.strip())
            current = s
        else:
            current = (current + " " + s).strip() if current else s
    if current:
        chunks.append(current.strip())
    return chunks or [text[:MAX_CHUNK]]


def chunk_markdown(text: str) -> list[str]:
    """Split a markdown file into paragraph-sized chunks."""
    # Strip YAML frontmatter
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    raw_paragraphs = text.split("\n\n")
    chunks = []
    for para in raw_paragraphs:
        para = para.strip()
        if len(para) < MIN_CHUNK:
            continue
        if len(para) > MAX_CHUNK:
            chunks.extend(_split_long(para))
        else:
            chunks.append(para)
    return chunks


# ── main ──────────────────────────────────────────────────────────────────────

async def run(vault_root: Path, dry_run: bool, process: bool):
    settings = Settings()

    db = AsyncPostgresPool(db_url=settings.db_url, min_size=1, max_size=4)
    await db.connect()
    embedder = EmbeddingProvider.from_settings(settings)

    if not dry_run:
        # Verify Ollama is reachable before queuing anything
        print("checking embedding service...")
        try:
            await embedder.embed("connectivity check")
            print("  OK\n")
        except Exception as exc:
            print(f"  FAIL: {exc}")
            print("Start Ollama and load the embed model, then retry.")
            await db.close()
            sys.exit(1)

    server = ScopedMCPServer(db=db, embedder=embedder, settings=settings)
    memory_tools = MemoryTools(
        db=db, embedder=embedder,
        chain_key=settings.chain_key, key_id=settings.chain_key_id,
    )

    total_queued = 0
    total_skipped = 0

    for rel_path, mem_type, trust in MANIFEST:
        full_path = vault_root / rel_path
        if not full_path.exists():
            print(f"  SKIP (not found): {rel_path}")
            total_skipped += 1
            continue

        text = full_path.read_text(encoding="utf-8", errors="replace")
        chunks = chunk_markdown(text)

        print(f"\n{rel_path}  [{mem_type}, trust={trust}]")
        print(f"  {len(chunks)} chunk(s)")

        content_class = _infer_content_class(rel_path)
        # One stable UUID per file — groups all chunks from the same source.
        # The human-readable path goes into metadata, not session_id (UUID col).
        file_session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"obsidian-import:{rel_path}"))

        for i, chunk in enumerate(chunks):
            preview = chunk[:80].replace("\n", " ")
            if dry_run:
                print(f"  [{i+1}] DRY-RUN [{content_class}]: {preview}...")
                continue

            result = await server.dispatch(
                "capture_memory",
                {
                    "content": chunk,
                    "memory_type": mem_type,
                    "trust_score": trust,
                    "content_class": content_class,
                    "session_id": file_session_id,
                    "metadata": {"source_path": rel_path, "chunk_index": i},
                },
            )
            print(f"  [{i+1}] queued [{content_class}] → {result.get('inbox_id', '?')[:8]}...")
            total_queued += 1

    print("\n── summary ──────────────────────────────────────────────────")
    if dry_run:
        print("  dry-run complete — no items submitted")
        print(f"  files found: {len(MANIFEST) - total_skipped}/{len(MANIFEST)}")
    else:
        print(f"  queued: {total_queued} chunks")
        print(f"  skipped (file not found): {total_skipped}")

    if process and not dry_run and total_queued > 0:
        print("\n── processing inbox ─────────────────────────────────────────")
        classifier = IngestionClassifier(
            embedder=embedder,
            db=db,
            dedup_reject=settings.inbox_dedup_reject_distance,
            dedup_merge=settings.inbox_dedup_merge_distance,
            dedup_hold=settings.inbox_dedup_hold_distance,
            litellm_base_url=settings.litellm_base_url,
            litellm_api_key=settings.litellm_api_key,
            llm_model=settings.reranker_model,
        )
        worker = InboxWorker(db=db, classifier=classifier, memory_tools=memory_tools)
        # Run until queue is drained
        total_processed = 0
        while True:
            n = await worker.run_once()
            if n == 0:
                break
            total_processed += n
            print(f"  processed batch: {n} (total so far: {total_processed})")
        print(f"  done — {total_processed} items processed")

        rows = await db.fetchall(
            "SELECT status, COUNT(*) AS cnt FROM memory_inbox GROUP BY status ORDER BY status"
        )
        print("\n── inbox results ────────────────────────────────────────────")
        for r in rows:
            print(f"  {r['status']:12s}  {r['cnt']}")

        chain = await memory_tools.verify_chain()
        print("\n── chain integrity ──────────────────────────────────────────")
        print(f"  valid:   {chain['chain_valid']}")
        print(f"  records: {chain['records_checked']}")

    await db.close()


def main():
    parser = argparse.ArgumentParser(description="Import Obsidian notes into Jeli")
    parser.add_argument(
        "--vault-root",
        default=str(Path.home() / "Library" / "Mobile Documents" /
                    "iCloud~md~obsidian" / "Documents"),
        help="Path to Obsidian vault root",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be imported without submitting",
    )
    parser.add_argument(
        "--process", action="store_true",
        help="Also run the inbox worker to classify and chain-write immediately",
    )
    args = parser.parse_args()

    vault_root = Path(args.vault_root)
    if not vault_root.exists():
        print(f"Vault root not found: {vault_root}")
        print("Pass --vault-root /path/to/your/vault")
        sys.exit(1)

    asyncio.run(run(vault_root, dry_run=args.dry_run, process=args.process))


if __name__ == "__main__":
    main()
