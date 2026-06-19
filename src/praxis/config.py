"""Application configuration via pydantic-settings.

All environment variables are documented here and mirrored in ``.env.example``.
Settings are loaded from environment variables and an optional ``.env`` file.
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────
    app_name: str = "PRAXIS"
    app_version: str = "0.1.0"
    debug: bool = False

    # ── Umans API ────────────────────────────────────────────────────
    umans_api_base_url: str = "https://api.umans.ai"
    umans_api_key: SecretStr = Field(
        default=SecretStr(""), description="Umans API key for inference"
    )

    # ── AgentMail / Svix Webhook ─────────────────────────────────────
    svix_webhook_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Svix-compatible webhook signing secret (whsec_…)",
    )

    # ── Neo4j (Graph Database) ────────────────────────────────────────
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = Field(default=SecretStr("praxis_dev_password"))

    # ── Qdrant (Vector Database) ──────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr = Field(default=SecretStr("praxis_dev_api_key"))

    # ── PostgreSQL (LangGraph Checkpointing) ──────────────────────────
    postgres_dsn: str = "postgresql://praxis:praxis_dev_password@localhost:5432/praxis"

    # ── Concurrency Limits ────────────────────────────────────────────
    kimi_concurrency_limit: int = Field(default=4, description="Max concurrent Kimi/GLM calls")
    glm_concurrency_limit: int = Field(default=4, description="Max concurrent GLM calls")
    qwen_concurrency_limit: int = Field(default=8, description="Max concurrent Qwen calls")


_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """Return cached application settings (singleton)."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def reset_settings() -> None:
    """Reset the cached settings (useful for testing)."""
    global _settings_instance
    _settings_instance = None
