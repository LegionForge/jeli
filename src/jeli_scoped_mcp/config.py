"""Configuration management for Jeli Scoped MCP Server."""

from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load and validate configuration from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="SCOPED_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    db_url: str = Field(default="postgresql://jeli_app@127.0.0.1:5442/jeli")
    db_min_size: int = Field(default=5)
    db_max_size: int = Field(default=20)

    # Security
    api_key: str = Field(default="")
    chain_key: str = Field(default="")
    chain_key_id: str = Field(default="k1")

    # Identity stamped on every write/audit row; server-side so agents
    # cannot impersonate another writer.
    agent_actor: str = Field(default="unknown-agent")

    # Embedding
    # Local-first: sovereignty is the default, cloud is the opt-in.
    embedding_provider: Literal["openai", "ollama", "mlx"] = Field(default="ollama")
    embedding_dimensions: int = Field(default=0)
    embed_keep_alive: str = Field(default="30m")
    # These env vars intentionally omit the SCOPED_MCP_ prefix (standard names).
    openai_api_key: str = Field(
        default="", validation_alias=AliasChoices("OPENAI_API_KEY", "scoped_mcp_openai_api_key")
    )
    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        validation_alias=AliasChoices("OLLAMA_BASE_URL", "scoped_mcp_ollama_base_url"),
    )
    ollama_model: str = Field(
        default="snowflake-arctic-embed2",
        validation_alias=AliasChoices("OLLAMA_MODEL", "scoped_mcp_ollama_model"),
    )

    # Re-ranking via LiteLLM proxy
    litellm_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("LITELLM_BASE_URL", "scoped_mcp_litellm_base_url"),
    )
    litellm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LITELLM_API_KEY", "scoped_mcp_litellm_api_key"),
    )
    reranker_enabled: bool = Field(default=False)
    reranker_model: str = Field(default="local-chat")
    reranker_timeout: float = Field(default=30.0)
    reranker_candidate_limit: int = Field(default=20)
    reranker_top_k: int = Field(default=10)

    # Inbox / Bouncer
    inbox_enabled: bool = Field(default=True)
    inbox_poll_interval: float = Field(default=5.0)
    inbox_max_retries: int = Field(default=3)
    inbox_dedup_reject_distance: float = Field(default=0.10)
    inbox_dedup_merge_distance: float = Field(default=0.15)
    inbox_dedup_hold_distance: float = Field(default=0.22)
    inbox_worker_concurrency: int = Field(default=1)

    # Daemons
    conflict_resolver_enabled: bool = Field(default=True)
    conflict_resolver_concurrency: int = Field(default=1)
    insights_enabled: bool = Field(default=True)
    maintenance_enabled: bool = Field(default=True)

    # Server
    transport: Literal["stdio", "http"] = Field(default="stdio")
    log_level: str = Field(default="INFO")
    log_format: Literal["json", "text"] = Field(default="json")
    enable_metrics: bool = Field(default=True)
    metrics_port: int = Field(default=8000)

    # Development
    debug: bool = Field(default=False)

    def validate_required(self):
        """Validate that all required settings are present."""
        if self.transport == "http" and not self.api_key:
            raise ValueError("SCOPED_MCP_API_KEY is required for http transport")
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
