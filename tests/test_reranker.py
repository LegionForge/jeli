"""Unit tests for re-ranking providers. No real HTTP or DB calls."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jeli_scoped_mcp.reranker.provider import LiteLLMReranker, NullReranker

# ── helpers ──────────────────────────────────────────────────────────────────

def make_candidates(n: int, with_distance: bool = True) -> list[dict]:
    return [
        {
            "id": f"id-{i}",
            "content": f"memory content number {i}",
            **({"distance": 0.1 + i * 0.05} if with_distance else {}),
        }
        for i in range(n)
    ]


def make_reranker(**kwargs) -> LiteLLMReranker:
    defaults = {"base_url": "http://localhost:4000", "api_key": "test-key"}
    defaults.update(kwargs)
    return LiteLLMReranker(**defaults)


def _mock_llm_response(scores: list[float]) -> MagicMock:
    resp_data = {
        "choices": [{"message": {"content": json.dumps(scores)}}]
    }
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=resp_data)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _mock_session(mock_resp: MagicMock) -> MagicMock:
    mock_post = MagicMock()
    mock_post.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_post.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_post)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


# ── NullReranker ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_null_reranker_sets_relevance_from_distance():
    candidates = make_candidates(3)
    result = await NullReranker().rerank("anything", candidates)
    assert len(result) == 3
    for c in result:
        assert "relevance_score" in c
    # relevance_score = 1.0 - distance; distance=0.10 → 0.90
    assert result[0]["relevance_score"] == pytest.approx(0.90, abs=0.001)


@pytest.mark.asyncio
async def test_null_reranker_no_distance_defaults_to_half():
    candidates = make_candidates(2, with_distance=False)
    result = await NullReranker().rerank("q", candidates)
    for c in result:
        assert c["relevance_score"] == pytest.approx(0.5, abs=0.001)


@pytest.mark.asyncio
async def test_null_reranker_preserves_order():
    candidates = make_candidates(4)
    result = await NullReranker().rerank("q", candidates)
    assert [c["id"] for c in result] == ["id-0", "id-1", "id-2", "id-3"]


# ── LiteLLMReranker — happy path ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_litellm_reranker_scores_and_sorts():
    candidates = make_candidates(3)
    scores = [0.2, 0.9, 0.5]

    with patch("aiohttp.ClientSession", return_value=_mock_session(_mock_llm_response(scores))):
        result = await make_reranker().rerank("test query", candidates)

    assert result[0]["id"] == "id-1"  # score 0.9
    assert result[1]["id"] == "id-2"  # score 0.5
    assert result[2]["id"] == "id-0"  # score 0.2
    assert result[0]["relevance_score"] == pytest.approx(0.9)


# ── Parse failures — fall back to original order ─────────────────────────────

@pytest.mark.asyncio
async def test_parse_failure_falls_back_to_original_order():
    candidates = make_candidates(3)
    garbage_resp = {
        "choices": [{"message": {"content": "sorry I cannot do that"}}]
    }
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=garbage_resp)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=_mock_session(mock_resp)):
        result = await make_reranker().rerank("q", candidates)

    # No crash; original order preserved; NullReranker fallback sets relevance_score
    assert len(result) == 3
    assert [c["id"] for c in result] == ["id-0", "id-1", "id-2"]


# ── Length mismatch ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_length_mismatch_pads_with_zero():
    candidates = make_candidates(4)
    # LLM returns only 2 scores for 4 candidates
    with patch("aiohttp.ClientSession", return_value=_mock_session(_mock_llm_response([0.8, 0.3]))):
        result = await make_reranker().rerank("q", candidates)

    scores = {c["id"]: c["relevance_score"] for c in result}
    assert scores["id-0"] == pytest.approx(0.8)
    assert scores["id-1"] == pytest.approx(0.3)
    assert scores["id-2"] == pytest.approx(0.0)
    assert scores["id-3"] == pytest.approx(0.0)


# ── Score clamping ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scores_clamped_to_0_1():
    candidates = make_candidates(2)
    with patch("aiohttp.ClientSession", return_value=_mock_session(_mock_llm_response([1.5, -0.3]))):
        result = await make_reranker().rerank("q", candidates)

    scores = {c["id"]: c["relevance_score"] for c in result}
    assert scores["id-0"] == pytest.approx(1.0)
    assert scores["id-1"] == pytest.approx(0.0)


# ── Candidate truncation ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_candidate_limit_truncates_input():
    candidates = make_candidates(30)
    reranker = make_reranker(candidate_limit=20)

    captured_prompts = []

    async def fake_call_llm(prompt: str, expected: int) -> list[float]:
        captured_prompts.append((prompt, expected))
        return [0.5] * expected

    reranker._call_llm = fake_call_llm  # type: ignore[method-assign]
    result = await reranker.rerank("q", candidates)

    assert len(result) == 20
    assert captured_prompts[0][1] == 20  # only 20 scored


# ── HTTP error falls back gracefully ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_http_error_falls_back_to_original_order():
    candidates = make_candidates(3)

    with patch("aiohttp.ClientSession") as mock_cls:
        instance = MagicMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.post.side_effect = Exception("connection refused")
        mock_cls.return_value = instance

        result = await make_reranker().rerank("q", candidates)

    assert len(result) == 3
    assert [c["id"] for c in result] == ["id-0", "id-1", "id-2"]


# ── search_memory integration (no DB — just checks reranker call pattern) ────

@pytest.mark.asyncio
async def test_search_memory_rerank_false_skips_reranker():
    """reranker.rerank must not be called when rerank=False."""
    from unittest.mock import AsyncMock as AM

    from jeli_scoped_mcp.tools.memory_tools import MemoryTools

    mock_reranker = MagicMock()
    mock_reranker.rerank = AM(return_value=[])

    tools = MemoryTools(db=MagicMock(), embedder=None, chain_key="k", reranker=mock_reranker)

    # patch the DB and embedder paths to return empty quickly
    tools.db.fetchall = AM(return_value=[])

    await tools.search_memory("q", "actor", mode="fts", limit=5, rerank=False)
    mock_reranker.rerank.assert_not_called()


@pytest.mark.asyncio
async def test_search_memory_rerank_true_fts_skips_reranker():
    """reranker.rerank must not be called for FTS mode even with rerank=True."""
    from unittest.mock import AsyncMock as AM

    from jeli_scoped_mcp.tools.memory_tools import MemoryTools

    mock_reranker = MagicMock()
    mock_reranker.rerank = AM(return_value=[])

    tools = MemoryTools(db=MagicMock(), embedder=None, chain_key="k", reranker=mock_reranker)
    tools.db.fetchall = AM(return_value=[])

    await tools.search_memory("q", "actor", mode="fts", limit=5, rerank=True)
    mock_reranker.rerank.assert_not_called()


# ── safety-aware penalty (MemoryGraft defense) ────────────────────────────────


from jeli_scoped_mcp.reranker.provider import apply_safety_penalty  # noqa: E402


def _candidate(**kw):
    base = {"content": "x", "relevance_score": 0.9, "effective_trust": 0.9,
            "injection_flagged": False}
    base.update(kw)
    return base


class TestSafetyPenalty:
    def test_flagged_demoted_below_unflagged(self):
        """Equal relevance: an injection-flagged result must rank below a clean one."""
        flagged = _candidate(content="poisoned", injection_flagged=True)
        clean = _candidate(content="legit")
        out = apply_safety_penalty([flagged, clean])
        assert out[0]["content"] == "legit"
        assert out[0]["relevance_score"] > out[1]["relevance_score"]

    def test_lower_trust_demoted(self):
        """Equal relevance: lower effective trust ranks lower."""
        low = _candidate(content="low", effective_trust=0.3)
        high = _candidate(content="high", effective_trust=0.9)
        out = apply_safety_penalty([low, high])
        assert out[0]["content"] == "high"

    def test_similarity_cannot_outrank_flag(self):
        """A poisoned entry engineered for perfect similarity still loses to a
        moderately relevant trusted one — the MemoryGraft scenario."""
        poisoned = _candidate(
            content="grafted procedure", relevance_score=1.0,
            effective_trust=0.3, injection_flagged=True,
        )
        trusted = _candidate(content="real procedure", relevance_score=0.6,
                             effective_trust=1.0)
        out = apply_safety_penalty([poisoned, trusted])
        assert out[0]["content"] == "real procedure"

    def test_missing_relevance_score_falls_back_to_distance(self):
        """Candidates without relevance_score derive it from vector distance."""
        c = _candidate(content="a")
        del c["relevance_score"]
        c["distance"] = 0.2
        out = apply_safety_penalty([c])
        assert out[0]["relevance_score"] == pytest.approx((1.0 - 0.2) * (0.5 + 0.5 * 0.9))

    def test_trust_falls_back_to_stored_score(self):
        """No effective_trust key → stored trust_score is used."""
        c = _candidate(content="a")
        del c["effective_trust"]
        c["trust_score"] = 0.4
        out = apply_safety_penalty([c])
        assert out[0]["relevance_score"] == pytest.approx(0.9 * (0.5 + 0.5 * 0.4))
