"""Configuration management for Jeli Scoped MCP Server."""

import os
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Load and validate configuration from environment variables."""

    # Database
    db_url: str = os.getenv("SCOPED_MCP_DB_URL", "postgresql://ob1_app@127.0.0.1:5433/openbrain")
    db_min_size: int = int(os.getenv("SCOPED_MCP_DB_MIN_SIZE", "5"))
    db_max_size: int = int(os.getenv("SCOPED_MCP_DB_MAX_SIZE", "20"))

    # Security
    api_key: str = os.getenv("SCOPED_MCP_API_KEY", "")
    chain_key: str = os.getenv("SCOPED_MCP_CHAIN_KEY", "")

    # Identity stamped on every write/audit row; server-side so agents
    # cannot impersonate another writer.
    agent_actor: str = os.getenv("SCOPED_MCP_AGENT_ACTOR", "unknown-agent")

    # Embedding
    embedding_provider: Literal["openai", "ollama"] = os.getenv("SCOPED_MCP_EMBEDDING_PROVIDER", "openai")  # type: ignore
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "nomic-embed-text")

    # Server
    transport: Literal["stdio", "http"] = os.getenv("SCOPED_MCP_TRANSPORT", "stdio")  # type: ignore
    log_level: str = os.getenv("SCOPED_MCP_LOG_LEVEL", "INFO")
    log_format: Literal["json", "text"] = os.getenv("SCOPED_MCP_LOG_FORMAT", "json")  # type: ignore
    enable_metrics: bool = os.getenv("SCOPED_MCP_ENABLE_METRICS", "true").lower() == "true"
    metrics_port: int = int(os.getenv("SCOPED_MCP_METRICS_PORT", "8000"))

    # Development
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"

    class Config:
        env_file = ".env"
        case_sensitive = False

    def validate_required(self):
        """Validate that all required settings are present."""
        if not self.api_key:
            raise ValueError("SCOPED_MCP_API_KEY is required")
        if not self.chain_key:
            raise ValueError("SCOPED_MCP_CHAIN_KEY is required (hash-chain HMAC key)")
        if self.embedding_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when using OpenAI provider")
        return True


def get_settings() -> Settings:
    """Get and validate settings."""
    settings = Settings()
    settings.validate_required()
    return settings
