"""Abstract embedding provider interface and implementations (v1: OpenAI)."""

from abc import ABC, abstractmethod
from datetime import datetime

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
        """Embed text and return vector + provenance."""
        pass

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
            return OllamaProvider(settings.ollama_base_url, settings.ollama_model)
        else:
            raise ValueError(f"Unknown embedding provider: {settings.embedding_provider}")


class OpenAIProvider(EmbeddingProvider):
    """OpenAI text-embedding-3-small (1536 dimensions, v1 default)."""

    MODEL = "text-embedding-3-small"
    DIMENSIONS = 1536

    def __init__(self, api_key: str):
        """Initialize with OpenAI API key."""
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key)

    async def embed(self, text: str) -> EmbeddingResult:
        """Embed text using OpenAI."""
        response = await self.client.embeddings.create(
            model=self.MODEL, input=text, encoding_format="float"
        )
        vector = response.data[0].embedding
        return EmbeddingResult(
            vector=vector,
            model_id=f"openai/{self.MODEL}",
            dimensions=len(vector),
            embedded_at=datetime.utcnow(),
        )

    def model_id(self) -> str:
        """Return model identifier."""
        return f"openai/{self.MODEL}"

    def dimensions(self) -> int:
        """Return embedding dimensions."""
        return self.DIMENSIONS


class OllamaProvider(EmbeddingProvider):
    """Ollama local embedding provider (Phase 2, sovereign alternative)."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "nomic-embed-text"):
        """Initialize with Ollama server URL and model."""
        self.base_url = base_url
        self.model = model

    async def embed(self, text: str) -> EmbeddingResult:
        """Embed text using Ollama (deferred to Phase 2)."""
        raise NotImplementedError("Ollama provider implemented in Phase 2")

    def model_id(self) -> str:
        """Return model identifier."""
        return f"ollama/{self.model}"

    def dimensions(self) -> int:
        """Return embedding dimensions (Ollama varies by model)."""
        return 768  # Default for nomic-embed-text; TBD per model
