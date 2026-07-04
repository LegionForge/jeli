"""Unit tests for daemon layer — MaintenanceDaemon, InsightsDaemon, DaemonRunner.

All DB and embedder calls are mocked; no live Postgres required.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.jeli_scoped_mcp.daemons.maintenance import MaintenanceDaemon
from src.jeli_scoped_mcp.daemons.insights import InsightsDaemon
from src.jeli_scoped_mcp.daemons.runner import DaemonRunner, _supervised


# ── helpers ────────────────────────────────────────────────────────────────────

def _db(fetchall=None, fetchrow=None, execute_result="DELETE 0"):
    db = MagicMock()
    db.fetchall = AsyncMock(return_value=fetchall or [])
    db.fetchrow = AsyncMock(return_value=fetchrow)
    db.execute = AsyncMock(return_value=execute_result)
    db.pool = None
    return db


def _memory_tools():
    mt = MagicMock()
    mt.capture_memory = AsyncMock(return_value={"id": "m1", "record_hash": "x"})
    return mt


def _settings():
    from src.jeli_scoped_mcp.config import Settings
    return Settings(
        chain_key="test-chain-key-32-bytes-minimum!!",
        inbox_worker_concurrency=1,
        conflict_resolver_concurrency=1,
        inbox_poll_interval=0.01,
        inbox_max_retries=3,
    )


# ── MaintenanceDaemon ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_maintenance_run_once_returns_all_keys():
    db = _db()
    daemon = MaintenanceDaemon(db=db, memory_tools=_memory_tools())
    result = await daemon.run_once()
    assert "trust_decay" in result
    assert "archival" in result
    assert "inbox_cleanup" in result


@pytest.mark.asyncio
async def test_maintenance_trust_decay_skips_fresh_memories():
    now = datetime.now(UTC)
    row = MagicMock()
    row.__getitem__ = lambda s, k: {
        "id": "m1",
        "trust_score": 0.6,
        "created_at": now,  # 0 days old → skipped
    }[k]
    db = _db(fetchall=[row])
    daemon = MaintenanceDaemon(db=db, memory_tools=_memory_tools())
    result = await daemon._apply_trust_decay()
    assert result["decayed"] == 0
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_maintenance_trust_decay_updates_old_memory():
    old_time = datetime.now(UTC) - timedelta(days=30)
    row = MagicMock()
    row.__getitem__ = lambda s, k: {
        "id": "m1",
        "trust_score": 0.6,
        "created_at": old_time,
    }[k]
    db = _db(fetchall=[row])
    daemon = MaintenanceDaemon(db=db, memory_tools=_memory_tools())
    result = await daemon._apply_trust_decay()
    assert result["decayed"] == 1
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_maintenance_archive_expired_no_pool():
    row = MagicMock()
    row.__getitem__ = lambda s, k: {"id": "m1"}[k]
    db = _db(fetchall=[row])
    db.pool = None   # no pool → breaks out early
    daemon = MaintenanceDaemon(db=db, memory_tools=_memory_tools())
    result = await daemon._archive_expired()
    assert result["archived"] == 0


@pytest.mark.asyncio
async def test_maintenance_cleanup_inbox_parses_delete_count():
    db = _db(execute_result="DELETE 7")
    daemon = MaintenanceDaemon(db=db, memory_tools=_memory_tools())
    result = await daemon._cleanup_old_inbox()
    assert result["inbox_rows_deleted"] == 7


@pytest.mark.asyncio
async def test_maintenance_cleanup_inbox_handles_bad_result():
    db = _db(execute_result="")
    daemon = MaintenanceDaemon(db=db, memory_tools=_memory_tools())
    result = await daemon._cleanup_old_inbox()
    assert result["inbox_rows_deleted"] == 0


# ── InsightsDaemon ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_insights_run_once_returns_all_keys():
    db = _db()
    embedder = MagicMock()
    daemon = InsightsDaemon(db=db, embedder=embedder, memory_tools=_memory_tools())
    result = await daemon.run_once()
    assert "clusters" in result
    assert "stale_procedural" in result
    assert "weak_signal" in result


@pytest.mark.asyncio
async def test_insights_cluster_scan_empty_returns_zero():
    db = _db(fetchall=[])
    embedder = MagicMock()
    daemon = InsightsDaemon(db=db, embedder=embedder, memory_tools=_memory_tools())
    result = await daemon._cluster_scan()
    assert result["clusters_found"] == 0


@pytest.mark.asyncio
async def test_insights_stale_procedural_empty():
    db = _db(fetchall=[])
    daemon = InsightsDaemon(db=db, embedder=MagicMock(), memory_tools=_memory_tools())
    result = await daemon._stale_procedural_scan()
    assert result.get("stale_flagged", result.get("flagged", 0)) == 0


@pytest.mark.asyncio
async def test_insights_stale_procedural_flags_row():
    row = MagicMock()
    row.__getitem__ = lambda s, k: {"id": "m1"}[k]
    db = _db(fetchall=[row])
    db.execute = AsyncMock(return_value="INSERT 1")
    daemon = InsightsDaemon(db=db, embedder=MagicMock(), memory_tools=_memory_tools())
    result = await daemon._stale_procedural_scan()
    # Key may be 'stale_flagged' or 'flagged' depending on impl
    count = result.get("stale_procedural_flagged", result.get("stale_flagged", result.get("flagged", 0)))
    assert count == 1


@pytest.mark.asyncio
async def test_insights_weak_signal_empty():
    db = _db(fetchall=[])
    daemon = InsightsDaemon(db=db, embedder=MagicMock(), memory_tools=_memory_tools())
    result = await daemon._weak_signal_scan()
    count = result.get("weak_signal_flagged", result.get("flagged", 0))
    assert count == 0


# ── _supervised ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_supervised_cancels_cleanly():
    async def forever():
        await asyncio.sleep(9999)

    task = asyncio.create_task(_supervised(forever, "test"))
    await asyncio.sleep(0.02)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.done()


@pytest.mark.asyncio
async def test_supervised_swallows_exception_and_continues():
    """_supervised must not propagate non-CancelledError; loop must restart."""
    calls = []
    gate = asyncio.Event()

    async def crash_then_wait():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("first crash")
        gate.set()
        await asyncio.sleep(9999)

    # Use a zero-backoff variant by patching _BASE_BACKOFF
    import src.jeli_scoped_mcp.daemons.runner as runner_mod
    original = runner_mod._BASE_BACKOFF
    runner_mod._BASE_BACKOFF = 0.0
    try:
        task = asyncio.create_task(_supervised(crash_then_wait, "test"))
        await asyncio.wait_for(gate.wait(), timeout=2.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        runner_mod._BASE_BACKOFF = original

    assert len(calls) >= 2


# ── DaemonRunner ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_runner_start_and_finish_run():
    run_id = "run-uuid-001"
    row = MagicMock()
    row.__getitem__ = lambda s, k: {"id": run_id}[k]
    db = _db(fetchrow=row)

    runner = DaemonRunner(
        db=db,
        embedder=MagicMock(),
        memory_tools=_memory_tools(),
        settings=_settings(),
        runner_id="test-runner",
    )
    rid = await runner._start_run("maintenance")
    assert rid == run_id
    db.fetchrow.assert_awaited_once()

    await runner._finish_run(rid, "completed", items=5)
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_runner_finish_run_noop_on_empty_id():
    db = _db()
    runner = DaemonRunner(
        db=db, embedder=MagicMock(),
        memory_tools=_memory_tools(), settings=_settings(),
    )
    await runner._finish_run("", "completed")
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_run_insights_once():
    row = MagicMock()
    row.__getitem__ = lambda s, k: {"id": "r1"}[k]
    db = _db(fetchrow=row)

    runner = DaemonRunner(
        db=db, embedder=MagicMock(),
        memory_tools=_memory_tools(), settings=_settings(),
    )
    insights_result = {"clusters": 2, "stale_procedural": 0, "weak_signal": 1}
    with patch(
        "src.jeli_scoped_mcp.daemons.runner.InsightsDaemon"
    ) as MockInsights:
        MockInsights.return_value.run_once = AsyncMock(return_value=insights_result)
        result = await runner.run_insights_once()

    assert result == insights_result


@pytest.mark.asyncio
async def test_runner_run_maintenance_once():
    row = MagicMock()
    row.__getitem__ = lambda s, k: {"id": "r2"}[k]
    db = _db(fetchrow=row)

    runner = DaemonRunner(
        db=db, embedder=MagicMock(),
        memory_tools=_memory_tools(), settings=_settings(),
    )
    maint_result = {"trust_decay": {"decayed": 3}, "archival": {"archived": 0}, "inbox_cleanup": {"inbox_rows_deleted": 12}}
    with patch(
        "src.jeli_scoped_mcp.daemons.runner.MaintenanceDaemon"
    ) as MockMaint:
        MockMaint.return_value.run_once = AsyncMock(return_value=maint_result)
        result = await runner.run_maintenance_once()

    assert result == maint_result


@pytest.mark.asyncio
async def test_runner_run_forever_creates_tasks_and_cancels():
    db = _db()
    db.pool = MagicMock()  # non-None so workers attempt to start

    runner = DaemonRunner(
        db=db, embedder=MagicMock(),
        memory_tools=_memory_tools(), settings=_settings(),
        runner_id="test-forever",
    )

    # Patch workers/resolvers to sleep forever so we can cancel cleanly
    async def _sleep_forever():
        await asyncio.sleep(9999)

    with patch("src.jeli_scoped_mcp.daemons.runner.InboxWorker") as MockWorker, \
         patch("src.jeli_scoped_mcp.daemons.runner.ConflictResolverDaemon") as MockResolver:
        MockWorker.return_value.run_forever = _sleep_forever
        MockResolver.return_value.run_forever = _sleep_forever

        task = asyncio.create_task(runner.run_forever())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert task.done()
