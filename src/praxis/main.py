"""FastAPI application entry point and lifespan management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI

from praxis import __version__
from praxis.config import get_settings
from praxis.router import UmansConcurrencyRouter
from praxis.webhooks.email import router as webhook_router

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: create and tear down the concurrency router."""
    settings = get_settings()
    umans_router = UmansConcurrencyRouter(settings=settings)
    app.state.umans_router = umans_router
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
