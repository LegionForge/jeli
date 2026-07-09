"""Tests for MLXProvider — sentence-transformers mocked so no GPU required."""

import sys
from unittest.mock import MagicMock, patch

import pytest


def _make_torch_mock(mps_available: bool = False) -> MagicMock:
    m = MagicMock()
    m.backends = MagicMock()
    m.backends.mps = MagicMock()
    m.backends.mps.is_available = MagicMock(return_value=mps_available)
    return m


def _st_mock(encoder: MagicMock) -> MagicMock:
    """Return a mock sentence_transformers module with SentenceTransformer = encoder factory."""
    m = MagicMock()
    m.SentenceTransformer = MagicMock(return_value=encoder)
    return m


# ── factory / dimension resolution ──────────────────────────────────────────


def test_mlx_provider_known_model_resolves_dimensions():
    from src.jeli_scoped_mcp.embedding.provider import MLXProvider

    p = MLXProvider(model="Snowflake/snowflake-arctic-embed-m-v1.5")
    assert p.dimensions() == 1024
    assert p.model_id() == "mlx/Snowflake/snowflake-arctic-embed-m-v1.5"


def test_mlx_provider_unknown_model_requires_explicit_dims():
    from src.jeli_scoped_mcp.embedding.provider import MLXProvider

    with pytest.raises(ValueError, match="SCOPED_MCP_EMBEDDING_DIMENSIONS"):
        MLXProvider(model="some/unknown-model")


def test_mlx_provider_explicit_dimensions_override():
    from src.jeli_scoped_mcp.embedding.provider import MLXProvider

    p = MLXProvider(model="some/custom-model", dimensions=512)
    assert p.dimensions() == 512


# ── embed (mocked sentence-transformers) ────────────────────────────────────


def _fake_vector(dims: int) -> MagicMock:
    """Return a mock that behaves like a numpy array with .tolist()."""
    m = MagicMock()
    m.tolist = MagicMock(return_value=[0.1] * dims)
    return m


@pytest.mark.asyncio
async def test_mlx_embed_returns_correct_shape():
    from src.jeli_scoped_mcp.embedding.provider import MLXProvider

    enc = MagicMock()
    enc.encode = MagicMock(return_value=_fake_vector(1024))
    mods = {"torch": _make_torch_mock(), "sentence_transformers": _st_mock(enc)}

    with patch.dict(sys.modules, mods):
        p = MLXProvider(model="Snowflake/snowflake-arctic-embed-m-v1.5")
        result = await p.embed("test sentence")

    assert result.dimensions == 1024
    assert len(result.vector) == 1024
    assert result.model_id == "mlx/Snowflake/snowflake-arctic-embed-m-v1.5"
    assert result.embedded_at is not None


@pytest.mark.asyncio
async def test_mlx_embed_dimension_mismatch_raises():
    from src.jeli_scoped_mcp.embedding.provider import MLXProvider

    enc = MagicMock()
    enc.encode = MagicMock(return_value=_fake_vector(512))
    mods = {"torch": _make_torch_mock(), "sentence_transformers": _st_mock(enc)}

    with patch.dict(sys.modules, mods):
        p = MLXProvider(model="Snowflake/snowflake-arctic-embed-m-v1.5")
        with pytest.raises(ValueError, match="dims"):
            await p.embed("text that returns wrong dim count")


# ── embed_query (prefix injection) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_mlx_embed_query_adds_prefix():
    from src.jeli_scoped_mcp.embedding.provider import MLXProvider

    received: list[str] = []

    enc = MagicMock()
    def fake_encode(text, normalize_embeddings=True):
        received.append(text)
        return _fake_vector(1024)
    enc.encode = fake_encode

    with patch.dict(sys.modules, {"torch": _make_torch_mock(), "sentence_transformers": _st_mock(enc)}):
        p = MLXProvider(model="Snowflake/snowflake-arctic-embed-m-v1.5")
        await p.embed_query("find me postgres stuff")

    assert received[0].startswith("query: ")


@pytest.mark.asyncio
async def test_mlx_embed_no_prefix_for_symmetric_model():
    from src.jeli_scoped_mcp.embedding.provider import MLXProvider

    received: list[str] = []

    enc = MagicMock()
    def fake_encode(text, normalize_embeddings=True):
        received.append(text)
        return _fake_vector(1024)
    enc.encode = fake_encode

    with patch.dict(sys.modules, {"torch": _make_torch_mock(), "sentence_transformers": _st_mock(enc)}):
        p = MLXProvider(model="BAAI/bge-m3")
        await p.embed_query("symmetric model query")

    assert received[0] == "symmetric model query"


# ── from_settings factory ────────────────────────────────────────────────────


def test_from_settings_returns_mlx_provider():
    from src.jeli_scoped_mcp.config import Settings
    from src.jeli_scoped_mcp.embedding.provider import EmbeddingProvider, MLXProvider

    # Use explicit dimensions so the known-model lookup is bypassed — the env
    # file may have OLLAMA_MODEL set to a model not in KNOWN_DIMENSIONS.
    settings = Settings(
        embedding_provider="mlx",
        ollama_model="Snowflake/snowflake-arctic-embed-m-v1.5",
        embedding_dimensions=1024,
        chain_key="test-key",
    )
    # Don't actually load the model — just verify the factory returns the right type.
    provider = EmbeddingProvider.from_settings(settings)
    assert isinstance(provider, MLXProvider)
    assert provider.dimensions() == 1024
