"""Unit tests for the Constitutional Layer (Read/Write gates + rule manager).

No live DB: a small in-memory fake of AsyncPostgresPool understands exactly the
queries ConstitutionalManager issues.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from test_memory_tools import FakeEmbedder, capture
from test_memory_tools import FakePool as MemFakePool

from jeli_scoped_mcp.constitutional.gate import ReadGate
from jeli_scoped_mcp.constitutional.manager import ConstitutionalError, ConstitutionalManager
from jeli_scoped_mcp.constitutional.rules import ConstitutionalRule, sign_rule
from jeli_scoped_mcp.server.mcp_server import ScopedMCPServer
from jeli_scoped_mcp.tools.memory_tools import MemoryToolError, MemoryTools

CHAIN_KEY = "test-chain-key"


def make_rule(rule_type: str, parameters: dict, applies_to: str = "all") -> ConstitutionalRule:
    created_at = datetime.now(UTC)
    description = f"rule for {rule_type}"
    return ConstitutionalRule(
        id=str(uuid.uuid4()),
        rule_type=rule_type,
        parameters=parameters,
        description=description,
        applies_to=applies_to,
        created_at=created_at,
        rule_hash=sign_rule(CHAIN_KEY, rule_type, parameters, description, applies_to, created_at),
    )


def result(**kw) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "content": "x",
        "trust_score": 0.6,
        "effective_trust": 0.6,
        "memory_type": "semantic",
        "content_class": "general",
    }
    base.update(kw)
    return base


# ── Read Gate ────────────────────────────────────────────────────────────────


def test_read_gate_exclude_memory_type():
    rule = make_rule("exclude_memory_type", {"memory_type": "transient"})
    results = [
        result(memory_type="transient"),
        result(memory_type="semantic"),
        result(memory_type="transient"),
    ]
    out = ReadGate().apply(results, actor="hermes", rules=[rule])
    assert len(out) == 1
    assert out[0]["memory_type"] == "semantic"


def test_read_gate_min_trust_floor():
    rule = make_rule("min_trust_floor", {"floor": 0.6})
    results = [
        result(effective_trust=0.3),
        result(effective_trust=0.6),
        result(effective_trust=0.9),
    ]
    out = ReadGate().apply(results, actor="hermes", rules=[rule])
    assert [r["effective_trust"] for r in out] == [0.6, 0.9]


def test_read_gate_exclude_content_class():
    rule = make_rule("exclude_content_class", {"content_class": "security-doc"})
    results = [
        result(content_class="security-doc"),
        result(content_class="general"),
    ]
    out = ReadGate().apply(results, actor="hermes", rules=[rule])
    assert len(out) == 1
    assert out[0]["content_class"] == "general"


def test_read_gate_max_results():
    rule = make_rule("max_results", {"max_results": 2})
    results = [result() for _ in range(5)]
    out = ReadGate().apply(results, actor="hermes", rules=[rule])
    assert len(out) == 2


def test_read_gate_exclude_tag():
    rule = make_rule("exclude_tag", {"tag": "secret"})
    results = [
        result(metadata={"tags": ["secret", "work"]}),
        result(metadata={"tags": ["work"]}),
        result(metadata={}),
    ]
    out = ReadGate().apply(results, actor="hermes", rules=[rule])
    assert len(out) == 2


def test_read_gate_applies_to_filter():
    rule = make_rule("exclude_memory_type", {"memory_type": "transient"}, applies_to="hermes")
    results = [result(memory_type="transient"), result(memory_type="semantic")]
    # actor is claude-code — rule scoped to hermes must NOT apply
    out = ReadGate().apply(results, actor="claude-code", rules=[rule])
    assert len(out) == 2


def test_read_gate_applies_to_all():
    rule = make_rule("exclude_memory_type", {"memory_type": "transient"}, applies_to="all")
    results = [result(memory_type="transient"), result(memory_type="semantic")]
    out = ReadGate().apply(results, actor="any-agent", rules=[rule])
    assert len(out) == 1


# ── rule integrity ───────────────────────────────────────────────────────────


async def test_rule_hash_verification():
    mgr = ConstitutionalManager()
    rule = make_rule("exclude_memory_type", {"memory_type": "transient"})
    assert await mgr.verify_rule(rule, CHAIN_KEY) is True

    # Tamper with the parameters — signature no longer matches.
    rule.parameters = {"memory_type": "identity"}
    assert await mgr.verify_rule(rule, CHAIN_KEY) is False


# ── manager add / revoke against a fake pool ────────────────────────────────


class FakePool:
    """Understands exactly the queries ConstitutionalManager issues."""

    def __init__(self):
        self.rules: list[dict] = []

    async def fetchval(self, query, *args):
        assert "SELECT now()" in query
        return datetime.now(UTC)

    async def fetchrow(self, query, *args):
        assert query.strip().startswith("INSERT INTO constitutional_rules")
        rule_type, parameters, description, applies_to, created_at, rule_hash, key_id = args
        import json as _json

        row = {
            "id": uuid.uuid4(),
            "rule_type": rule_type,
            "parameters": _json.loads(parameters),
            "description": description,
            "applies_to": applies_to,
            "active": True,
            "created_at": created_at,
            "revoked_at": None,
            "rule_hash": rule_hash,
            "key_id": key_id,
        }
        self.rules.append(row)
        return {"id": row["id"], "created_at": row["created_at"]}

    async def fetchall(self, query, *args):
        assert "FROM constitutional_rules" in query
        return [r for r in self.rules if r["revoked_at"] is None and r["active"]]

    async def execute(self, query, *args):
        assert "UPDATE constitutional_rules" in query
        (rule_id,) = args
        for r in self.rules:
            if str(r["id"]) == str(rule_id) and r["revoked_at"] is None:
                r["revoked_at"] = datetime.now(UTC)
                r["active"] = False
                return "UPDATE 1"
        return "UPDATE 0"


async def test_add_and_revoke_rule():
    pool = FakePool()
    mgr = ConstitutionalManager()

    added = await mgr.add_rule(
        pool,
        chain_key=CHAIN_KEY,
        key_id="k1",
        rule_type="exclude_memory_type",
        parameters={"memory_type": "transient"},
        description="Agents cannot see transient memories",
    )
    active = await mgr.list_rules(pool)
    assert len(active) == 1
    # Signed rule round-trips through the store and still verifies.
    assert await mgr.verify_rule(active[0], CHAIN_KEY) is True

    await mgr.revoke_rule(pool, added["id"])
    assert await mgr.list_rules(pool) == []


async def test_revoke_unknown_rule():
    pool = FakePool()
    mgr = ConstitutionalManager()
    with pytest.raises(ConstitutionalError, match="not found or already revoked"):
        await mgr.revoke_rule(pool, str(uuid.uuid4()))


async def test_add_rule_rejects_bad_type():
    pool = FakePool()
    mgr = ConstitutionalManager()
    with pytest.raises(ConstitutionalError, match="rule_type must be"):
        await mgr.add_rule(
            pool,
            chain_key=CHAIN_KEY,
            key_id="k1",
            rule_type="delete_everything",
            parameters={},
            description="nope",
        )


# ── Write Gate (through capture_memory) ──────────────────────────────────────


def rule_row(rule_type: str, parameters: dict, applies_to: str = "all") -> dict:
    """A constitutional_rules row as load_active_rules would read it."""
    return {
        "id": uuid.uuid4(),
        "rule_type": rule_type,
        "parameters": parameters,
        "description": f"{rule_type} rule",
        "applies_to": applies_to,
        "active": True,
        "created_at": datetime.now(UTC),
        "revoked_at": None,
        "rule_hash": "unused-in-gate",
        "key_id": "k1",
    }


class RulePool(MemFakePool):
    """MemoryTools fake pool that also serves active constitutional rules."""

    def __init__(self, rule_rows: list[dict]):
        super().__init__()
        self._rule_rows = rule_rows

    async def fetchall(self, query, *args):
        if "FROM constitutional_rules" in query:
            return list(self._rule_rows)
        return await super().fetchall(query, *args)


async def test_write_gate_blocks_denied_type():
    pool = RulePool([rule_row("deny_write_memory_type", {"memory_type": "identity"})])
    tools = MemoryTools(db=pool, embedder=FakeEmbedder(), chain_key=CHAIN_KEY)
    with pytest.raises(MemoryToolError, match="write gate blocked"):
        await capture(tools, memory_type="identity", trust_score=0.6)
    # Nothing was persisted — the write was rejected before the chain insert.
    assert pool.memories == []


async def test_write_gate_caps_trust():
    pool = RulePool(
        [
            rule_row(
                "max_trust_for_content_class",
                {"content_class": "external-untrusted", "max_trust": 0.3},
            )
        ]
    )
    tools = MemoryTools(db=pool, embedder=FakeEmbedder(), chain_key=CHAIN_KEY)
    out = await capture(
        tools,
        memory_type="semantic",
        trust_score=0.6,
        content_class="external-untrusted",
    )
    assert out["trust_score"] == 0.3


async def test_write_gate_applies_to_filter():
    pool = RulePool(
        [rule_row("deny_write_memory_type", {"memory_type": "identity"}, applies_to="hermes")]
    )
    tools = MemoryTools(db=pool, embedder=FakeEmbedder(), chain_key=CHAIN_KEY)
    # Rule is scoped to 'hermes'; a claude-code write of the same type is allowed.
    out = await capture(tools, memory_type="identity", trust_score=0.6, actor="claude-code")
    assert out["id"]
    assert len(pool.memories) == 1


# ── MCP server: external content-class inference ─────────────────────────────


def test_infer_content_class_url_in_content():
    # Real class is 'external-untrusted' (the valid VALID_CONTENT_CLASSES member);
    # 'external' from the spec is not a recognised class and would fail the write.
    out = ScopedMCPServer._infer_content_class(
        "see https://evil.example/leak for details", "general", "hermes"
    )
    assert out == "external-untrusted"


def test_infer_content_class_phrase_in_content():
    out = ScopedMCPServer._infer_content_class(
        "According to the docs, TOML is preferred", "general", "hermes"
    )
    assert out == "external-untrusted"


def test_infer_content_class_no_change_for_clean():
    out = ScopedMCPServer._infer_content_class(
        "JP prefers TOML over YAML", "general", "hermes"
    )
    assert out == "general"


def test_infer_content_class_cli_write_untouched():
    # source_agent=None means the CLI (a human), which self-declares honestly.
    out = ScopedMCPServer._infer_content_class(
        "see https://evil.example/leak", "general", None
    )
    assert out == "general"


def test_read_gate_unknown_rule_type_leaves_results_unchanged(caplog):
    """An unknown rule type is logged loudly but results are returned untouched (fail-closed
    semantics: a mis-typed rule never silently *widens* what agents see)."""
    unknown_rule = ConstitutionalRule(
        rule_type="nonexistent_future_rule",
        parameters={},
        description="from the future",
        applies_to="all",
        created_at=datetime.now(UTC),
        rule_hash="x",
    )
    results = [{"id": "m1", "content": "sensitive info", "memory_type": "preference"}]
    import logging

    with caplog.at_level(logging.WARNING, logger="jeli_scoped_mcp.constitutional.gate"):
        out = ReadGate().apply(results, actor="agent", rules=[unknown_rule])

    assert out == results  # unchanged
    assert "unknown rule_type" in caplog.text


# ── load_active_rules TTL cache ──────────────────────────────────────────────


class CountingPool(FakePool):
    """FakePool that counts how many times load_active_rules hits the DB."""

    def __init__(self):
        super().__init__()
        self.fetchall_calls = 0

    async def fetchall(self, query, *args):
        self.fetchall_calls += 1
        return await super().fetchall(query, *args)


async def test_load_active_rules_caches_within_ttl():
    pool = CountingPool()
    mgr = ConstitutionalManager()
    await mgr.load_active_rules(pool)
    await mgr.load_active_rules(pool)
    assert pool.fetchall_calls == 1  # second call served from cache


async def test_load_active_rules_refreshes_after_ttl():
    pool = CountingPool()
    mgr = ConstitutionalManager(ttl=30.0)
    clock = {"t": 1000.0}
    with patch(
        "jeli_scoped_mcp.constitutional.manager.time.monotonic",
        side_effect=lambda: clock["t"],
    ):
        await mgr.load_active_rules(pool)  # miss at t=1000
        clock["t"] = 1005.0
        await mgr.load_active_rules(pool)  # still within TTL → cache
        assert pool.fetchall_calls == 1
        clock["t"] = 1040.0  # past 30s TTL
        await mgr.load_active_rules(pool)  # miss again
        assert pool.fetchall_calls == 2


async def test_invalidate_cache_forces_fresh_load():
    pool = CountingPool()
    mgr = ConstitutionalManager()
    await mgr.load_active_rules(pool)
    mgr.invalidate_cache()
    await mgr.load_active_rules(pool)
    assert pool.fetchall_calls == 2


async def test_add_rule_invalidates_cache():
    pool = CountingPool()
    mgr = ConstitutionalManager()
    await mgr.load_active_rules(pool)  # warm the cache (fetchall #1)
    await mgr.add_rule(
        pool,
        chain_key=CHAIN_KEY,
        key_id="k1",
        rule_type="exclude_memory_type",
        parameters={"memory_type": "transient"},
        description="Agents cannot see transient memories",
    )
    # add_rule invalidated the cache → this load hits the DB again (fetchall #2)
    rules = await mgr.load_active_rules(pool)
    assert pool.fetchall_calls == 2
    assert len(rules) == 1
