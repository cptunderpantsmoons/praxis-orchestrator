"""Application configuration via Pydantic Settings.

All environment variables are loaded here and validated at startup.
The GLM-5.2 model context window is set to 1,000,000 tokens per project
requirement and is overridable via the UMANS_GLM_CONTEXT_WINDOW env var.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────
    app_name: str = "praxis"
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000

    # ── Umans Inference API ───────────────────────────────────────
    umans_api_key: str = "test-key"
    umans_base_url: str = "https://api.umans.ai/v1"

    # Model context windows (tokens)
    # GLM-5.2 adjusted to 1,000,000 token context per project requirement
    umans_glm_context_window: int = 1_000_000
    umans_kimi_context_window: int = 131_072
    umans_qwen_context_window: int = 131_072

    # ── AgentMail (Phase 1 email I/O) ─────────────────────────────
    agentmail_api_key: str = "test-key"
    agentmail_webhook_secret: str = "whsec_dGVzdHNlY3JldA=="

    # ── Neo4j (Graph Database) ────────────────────────────────────
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "praxis_dev_password"

    # ── Qdrant (Vector Database) ───────────────────────────────────
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str = "praxis_dev_api_key"

    # ── PostgreSQL (LangGraph Checkpointing) ──────────────────────
    postgres_dsn: str = "postgresql://praxis:***@127.0.0.1:5432/praxis"

    # ── LDR (Local Deep Research) ────────────────────────────────
    ldr_url: str = "http://127.0.0.1:5001"
    ldr_api_key: str = "praxis_ldr_2026"

    # ── Region / Localization ─────────────────────────────────────
    # Default region for region-aware agent delegation.  Agents will adapt
    # advice to local laws, regulations, taxes, and business norms.
    default_region: str = ""

    # ── Attachments & Failed-event recovery ─────────────────────────
    attachments_dir: str = "/tmp/praxis_documents"
    failed_events_path: str = "/tmp/praxis_failed_events.jsonl"

    # ── Hermes (Phase 3 agent platform) ───────────────────────────
    hermes_base_url: str = "http://127.0.0.1:8787"
    hermes_api_key: str = "hermes-dev-key"
    hermes_timeout: float = 30.0
    hermes_max_retries: int = 3

    # ── v0.2.0 additions ─────────────────────────────────────────
    agentmail_inbox_id: str | None = None
    praxis_admin_token: str = ""
    tool_protocol: Literal["native", "legacy"] = "native"
    sender_style_enabled: bool = True
    default_tone: Literal["formal", "casual", "professional"] = "professional"
    default_signature: str = "— PRAXIS"
    praxis_api_url: str = "http://localhost:8000"

    # ── Auto-Healing Recovery Agent ───────────────────────────────
    recovery_enabled: bool = True
    recovery_poll_interval_seconds: int = 10
    recovery_max_retries: int = 3
    recovery_rate_limit_seconds: float = 5.0
    recovery_events_path: str = "logs/recovery_events.jsonl"
    recovery_queue_max_size: int = 500


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


def reset_settings() -> None:
    """Clear the cached settings singleton.

    Useful in tests and when environment variables change at runtime.
    """
    get_settings.cache_clear()
