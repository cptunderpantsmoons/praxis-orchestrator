"""FastAPI application entry point and lifespan management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI

from praxis import __version__
from praxis.config import get_settings
from praxis.graph import build_graph
from praxis.graph.checkpointer import InMemoryFallback, get_checkpointer
from praxis.router import UmansConcurrencyRouter
from praxis.webhooks.email import router as webhook_router

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: create and tear down shared resources."""
    settings = get_settings()

    # Shared concurrency router for all Umans inference
    umans_router = UmansConcurrencyRouter(settings=settings)
    app.state.umans_router = umans_router

    # LangGraph checkpointer: prefer Postgres, fall back to memory for tests
    try:
        checkpointer = await get_checkpointer(settings=settings)
    except Exception as exc:  # pragma: no cover - environment-specific fallback
        logger.warning("checkpointer.postgres_failed", error=str(exc))
        checkpointer = InMemoryFallback()

    app.state.checkpointer = checkpointer
    app.state.graph = build_graph(checkpointer=checkpointer)

    logger.info(
        "app.startup",
        version=__version__,
        environment=settings.environment,
    )

    yield

    await umans_router.close()
    logger.info("app.shutdown")


app = FastAPI(
    title="PRAXIS v2.0",
    description="Native asynchronous enterprise email agent",
    version=__version__,
    lifespan=lifespan,
)

# Include route groups
app.include_router(webhook_router, prefix="/webhook", tags=["webhooks"])


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, Any]:
    """Health check endpoint.

    Quality Gate 1: Application boots successfully and passes the health check.
    """
    return {
        "status": "ok",
        "version": __version__,
        "services": {"api": "ready"},
    }
