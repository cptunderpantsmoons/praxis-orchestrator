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
from praxis.graph.checkpointer import get_checkpointer
from praxis.router import UmansConcurrencyRouter
from praxis.webhooks.admin import router as admin_router
from praxis.webhooks.email import router as webhook_router

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: create and tear down shared resources."""
    settings = get_settings()
    app.state.settings = settings

    # Shared concurrency router for all Umans inference
    umans_router = UmansConcurrencyRouter(settings=settings)
    app.state.umans_router = umans_router

    # LangGraph checkpointer: prefer Postgres, fall back to memory for tests
    try:
        checkpointer = await get_checkpointer(settings=settings)
    except Exception as exc:
        logger.warning("checkpointer.postgres_failed: %s", exc)
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()
    app.state.checkpointer = checkpointer
    app.state.graph = build_graph(checkpointer=checkpointer)
    # Hermes: stateful memory & learning service (REQ-309)
    # Shares the UmansConcurrencyRouter for LLM calls.

    # LDR: Local Deep Research service (REQ-310)
    # Uses its own Umans config inside the LDR container.
    try:
        from praxis.services.ldr_service import LdrService

        ldr = LdrService()
        if await ldr.health():
            app.state.ldr_service = ldr
            logger.info("ldr.service_ready", url=ldr.base_url)
        else:
            app.state.ldr_service = None
            logger.warning("ldr.service_unavailable", url=ldr.base_url)
    except Exception as exc:
        app.state.ldr_service = None
        logger.warning("ldr.startup_failed: %s", exc)

    # Document service (REQ-311)
    try:
        from praxis.services.document_service import DocumentService
        app.state.document_service = DocumentService()
        logger.info("document.service_ready")
    except Exception as exc:
        app.state.document_service = None
        logger.warning("document.startup_failed: %s", exc)

    # Agent delegation service (REQ-312) — agency-agents integration
    try:
        from praxis.services.agent_delegator import AgentDelegator
        from praxis.services.agent_registry import AgentRegistry
        registry = AgentRegistry()
        count = registry.load()
        app.state.agent_delegator = AgentDelegator(
            registry=registry,
            router=app.state.umans_router,
        )
        logger.info("agent.delegator_ready agents=%d divisions=%d", count, len(registry.divisions))
    except Exception as exc:
        app.state.agent_delegator = None
        logger.warning("agent.delegator_failed: %s", exc)
    # Neo4j/Qdrant connections lazily on first use.
    from praxis.services.hermes_service import HermesService

    app.state.hermes_service = HermesService(
        settings=settings,
        router=umans_router,
    )

    logger.info(
        "app.startup",
        version=__version__,
        environment=settings.environment,
    )

    yield

    await app.state.hermes_service.close()
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
app.include_router(admin_router, tags=["admin"])


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
