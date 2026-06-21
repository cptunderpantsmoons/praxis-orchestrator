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

    Implementation: build a real ``AsyncConnectionPool`` ourselves and hand it
    to ``AsyncPostgresSaver(conn=pool)``. The pool (not a single connection)
    is what the saver uses for every checkpoint read/write, and the pool lives
    for the lifetime of the process — that is what makes the returned saver
    safely usable by request handlers long after this function returns.

    Why not ``AsyncPostgresSaver.from_conn_string``? That helper is decorated
    with ``@asynccontextmanager`` and ``yield``s the saver *inside* an
    ``async with await AsyncConnection.connect(...)`` block. As soon as the
    context manager exits, the underlying connection is closed. The caller
    must hold the context manager open for the saver to keep working — which
    is awkward in a long-running server where the saver is stashed on
    ``app.state.checkpointer``. Building the pool directly side-steps the
    problem and gives us a self-contained saver.

    Args:
        settings: Optional settings instance.

    Returns:
        An ``AsyncPostgresSaver`` instance backed by a long-lived connection
        pool connected to ``settings.postgres_dsn``.
    """
    settings = settings or get_settings()

    # open=False: do not eagerly establish connections in the constructor;
    # open the pool explicitly so the first connection's failure surfaces
    # here (where we can fall back) rather than at first request time.
    # We add a hard timeout so tests / local dev without Postgres fail fast
    # instead of hanging on pool.wait() forever.
    import asyncio as _aio

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=settings.postgres_dsn,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": __import__("psycopg.rows", fromlist=["dict_row"]).dict_row},
    )
    await _aio.wait_for(pool.open(), timeout=5.0)
    await _aio.wait_for(pool.wait(), timeout=5.0)

    saver = AsyncPostgresSaver(conn=pool)
    await _aio.wait_for(saver.setup(), timeout=10.0)
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
