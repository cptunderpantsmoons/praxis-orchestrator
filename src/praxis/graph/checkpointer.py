"""LangGraph checkpointer lifecycle management.

Returns a PostgreSQL-backed AsyncPostgresSaver for durable state persistence.
In development / test environments, falls back to an in-memory saver when the
Postgres connection is not available, so the graph remains testable.
"""

from __future__ import annotations

from typing import Any

import structlog
from langgraph.checkpoint.memory import MemorySaver

from praxis.config import Settings, get_settings

logger = structlog.get_logger()


async def get_checkpointer(settings: Settings | None = None) -> Any:
    """Create and return a LangGraph checkpointer backed by PostgreSQL.

    Args:
        settings: Optional settings instance.

    Returns:
        An ``AsyncPostgresSaver`` instance connected to ``settings.postgres_dsn``.
    """
    settings = settings or get_settings()

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    saver = AsyncPostgresSaver.from_conn_string(conn_string=settings.postgres_dsn)
    await saver.setup()
    logger.info("checkpointer.postgres_ready", dsn=settings.postgres_dsn)
    return saver


class InMemoryFallback(MemorySaver):
    """In-memory checkpointer fallback for tests and local development.

    Subclasses ``MemorySaver`` so it passes LangGraph's checkpointer validation.
    """

    def __init__(self) -> None:
        super().__init__()
        logger.info("checkpointer.fallback_to_memory")


__all__ = ["InMemoryFallback", "get_checkpointer"]
