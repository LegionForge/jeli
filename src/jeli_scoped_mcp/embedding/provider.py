"""Abstract embedding provider interface and implementations (v1: OpenAI)."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from pydantic import BaseModel

from ..config import Settings


class EmbeddingResult(BaseModel):
    """Result of embedding a text."""

    vector: list[float]
    model_id: str
    dimensions: int
    embedded_at: datetime


class EmbeddingProvider(ABC):
    """Abstract base for embedding providers."""

    @abstractmethod
    async def embed(self, text: str) -> EmbeddingResult:
        """Embed a DOCUMENT and return vector + provenance."""
        pass

    async def embed_query(self, text: str) -> EmbeddingResult:
        """Embed a QUERY for retrieval against stored documents.

        Asymmetric-retrieval models (arctic-embed family) are trained with a
        query prefix; symmetric models just reuse embed(). Override when the
        model needs query-side conditioning.
        """
        return await self.embed(text)

    @abstractmethod
    def model_id(self) -> str:
        """Return model identifier."""
        pass

    @abstractmethod
    def dimensions(self) -> int:
        """Return embedding dimensions."""
        pass

    @classmethod
    def from_settings(cls, settings: Settings) -> "EmbeddingProvider":
        """Factory: create provider based on settings."""
        if settings.embedding_provider == "openai":
            return OpenAIProvider(settings.openai_api_key)
        elif settings.embedding_provider == "ollama":
            return OllamaProvider(
                settings.ollama_base_url,
                settings.ollama_model,
                dimensions=settings.embedding_dimensions or None,
                keep_alive=settings.embed_keep_alive,
            )
        elif settings.embedding_provider == "mlx":
            return MLXProvider(
                model=settings.ollama_model,  # reuse same env var for model name
                dimensions=settings.embedding_dimensions or None,
            )
        else:
            raise ValueError(f"Unknown embedding provider: {settings.embedding_provider}")


class OpenAIProvider(EmbeddingProvider):
    """OpenAI text-embedding-3-small, truncated to the 1024-dim index.

    1536 is OpenAI-native, but the sovereign index standardizes on 1024
    (arctic-embed2 native / Qwen3 MRL ceiling); the API truncates via the
    dimensions parameter with negligible quality loss (matryoshka).
    """

    MODEL = "text-embedding-3-small"
    DIMENSIONS = 1024

    def __init__(self, api_key: str):
        """Initialize with OpenAI API key."""
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key)

    async def embed(self, text: str) -> EmbeddingResult:
        """Embed text using OpenAI."""
        response = await self.client.embeddings.create(
            model=self.MODEL,
            input=text,
            encoding_format="float",
            dimensions=self.DIMENSIONS,
        )
        vector = response.data[0].embedding
        return EmbeddingResult(
            vector=vector,
            model_id=f"openai/{self.MODEL}",
            dimensions=len(vector),
            embedded_at=datetime.now(UTC),
        )

    def model_id(self) -> str:
        """Return model identifier."""
        return f"openai/{self.MODEL}"

    def dimensions(self) -> int:
        """Return embedding dimensions."""
        return self.DIMENSIONS


class OllamaProvider(EmbeddingProvider):
    """Ollama local embedding provider — the sovereign default."""

    # Asymmetric-retrieval families that expect a prefixed query side.
    QUERY_PREFIXES = {
        "snowflake-arctic-embed": "query: ",
        "snowflake-arctic-embed2": "query: ",
        "nomic-embed-text": "search_query: ",
    }

    # Known model dimensions; unknown models must pass dimensions explicitly.
    MODEL_DIMENSIONS = {
        "nomic-embed-text": 768,
        "snowflake-arctic-embed": 1024,
        "snowflake-arctic-embed2": 1024,
        "qwen3-embedding": 1024,
        "bge-m3": 1024,
    }

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "snowflake-arctic-embed2",
        dimensions: int | None = None,
        keep_alive: str = "30m",
    ):
        """Initialize with Ollama server URL and model.

        keep_alive keeps the embed model resident between calls — query
        embedding dominates end-to-end search latency (161ms vs ~15ms of
        HNSW at 2.3k memories), and most of that is model paging.
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive
        base_model = model.split(":")[0]
        dims = dimensions or self.MODEL_DIMENSIONS.get(base_model)
        if dims is None:
            raise ValueError(
                f"Unknown embedding dimensions for Ollama model '{model}' — "
                "set SCOPED_MCP_EMBEDDING_DIMENSIONS explicitly"
            )
        self._dimensions = dims

    async def embed(self, text: str) -> EmbeddingResult:
        """Embed text via the Ollama /api/embed endpoint."""
        import aiohttp

        # force_close=True: Ollama's HTTP server misbehaves on keep-alive
        # reuse — each request gets a fresh connection.
        connector = aiohttp.TCPConnector(force_close=True)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": self.model,
                    "input": text,
                    "keep_alive": self.keep_alive,
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        vector = data["embeddings"][0]
        if len(vector) != self._dimensions:
            raise ValueError(
                f"Ollama model {self.model} returned {len(vector)} dims, "
                f"expected {self._dimensions}"
            )
        return EmbeddingResult(
            vector=vector,
            model_id=f"ollama/{self.model}",
            dimensions=len(vector),
            embedded_at=datetime.now(UTC),
        )

    async def embed_query(self, text: str) -> EmbeddingResult:
        """Query-side embedding with the model's retrieval prefix."""
        prefix = self.QUERY_PREFIXES.get(self.model.split(":")[0], "")
        return await self.embed(prefix + text)

    def model_id(self) -> str:
        """Return model identifier."""
        return f"ollama/{self.model}"

    def dimensions(self) -> int:
        """Return embedding dimensions."""
        return self._dimensions


class MLXProvider(EmbeddingProvider):
    """Apple Silicon native embedding via sentence-transformers + MPS.

    No Ollama server required — model runs in-process on Metal. Install:
        pip install "jeli-scoped-mcp[mlx]"

    The 1024-dim index standard is maintained: use a model that natively
    emits 1024 dims (e.g. Snowflake/snowflake-arctic-embed-m-v1.5) or set
    SCOPED_MCP_EMBEDDING_DIMENSIONS to truncate via matryoshka.
    """

    KNOWN_DIMENSIONS: dict[str, int] = {
        "Snowflake/snowflake-arctic-embed-m-v1.5": 1024,
        "Snowflake/snowflake-arctic-embed-l-v2.0": 1024,
        "nomic-ai/nomic-embed-text-v1.5": 768,
        "BAAI/bge-m3": 1024,
    }

    # Asymmetric query prefixes for arctic-embed family.
    QUERY_PREFIXES: dict[str, str] = {
        "Snowflake/snowflake-arctic-embed-m-v1.5": "query: ",
        "Snowflake/snowflake-arctic-embed-l-v2.0": "query: ",
        "nomic-ai/nomic-embed-text-v1.5": "search_query: ",
    }

    def __init__(
        self,
        model: str = "Snowflake/snowflake-arctic-embed-m-v1.5",
        dimensions: int | None = None,
    ):
        self._model_name = model
        dims = dimensions or self.KNOWN_DIMENSIONS.get(model)
        if dims is None:
            raise ValueError(
                f"Unknown embedding dimensions for MLX model '{model}' — "
                "set SCOPED_MCP_EMBEDDING_DIMENSIONS explicitly"
            )
        self._dimensions = dims
        self._encoder: object | None = None  # lazy-loaded; never None after _load()

    def _load(self):
        """Lazy-load sentence-transformers model onto MPS device."""
        if self._encoder is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "MLX provider requires: pip install 'jeli-scoped-mcp[mlx]'"
            ) from e
        import torch

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        self._encoder = SentenceTransformer(self._model_name, device=device)

    async def embed(self, text: str) -> EmbeddingResult:
        """Embed text in-process via sentence-transformers on Metal (MPS)."""
        import asyncio

        self._load()
        loop = asyncio.get_event_loop()
        # Run blocking inference in thread pool so the event loop stays free.
        encoder = self._encoder
        if encoder is None:
            raise RuntimeError("MLXProvider._load() failed to set encoder")
        vector = await loop.run_in_executor(
            None, lambda: encoder.encode(text, normalize_embeddings=True).tolist()  # type: ignore[union-attr]
        )
        if len(vector) != self._dimensions:
            raise ValueError(
                f"MLX model {self._model_name} returned {len(vector)} dims, "
                f"expected {self._dimensions}"
            )
        return EmbeddingResult(
            vector=vector,
            model_id=f"mlx/{self._model_name}",
            dimensions=len(vector),
            embedded_at=datetime.now(UTC),
        )

    async def embed_query(self, text: str) -> EmbeddingResult:
        """Query-side embedding with model's retrieval prefix."""
        prefix = self.QUERY_PREFIXES.get(self._model_name, "")
        return await self.embed(prefix + text)

    def model_id(self) -> str:
        return f"mlx/{self._model_name}"

    def dimensions(self) -> int:
        return self._dimensions
