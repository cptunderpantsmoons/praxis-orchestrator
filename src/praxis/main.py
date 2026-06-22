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
from praxis.webhooks.logs import router as logs_router
from praxis.webhooks.metrics_admin import router as metrics_router
from praxis.webhooks.settings_admin import router as settings_router
from praxis.webhooks.system import router as system_router

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
app.include_router(system_router)
app.include_router(settings_router)
app.include_router(logs_router)
app.include_router(metrics_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, Any]:
    """Health check endpoint — reports status of all services."""
    services = {"api": "ready"}

    # Checkpointer (Postgres)
    try:
        checkpointer = app.state.checkpointer
        services["checkpointer"] = "ready" if checkpointer else "unavailable"
    except Exception:
        services["checkpointer"] = "unavailable"

    # Router
    try:
        router = app.state.umans_router
        services["router"] = "ready" if router else "unavailable"
    except Exception:
        services["router"] = "unavailable"

    # Hermes service
    try:
        hermes = getattr(app.state, "hermes_service", None)
        if hermes:
            healthy = await hermes.health() if hasattr(hermes, 'health') else True
            services["hermes_service"] = "ready" if healthy else "degraded"
        else:
            services["hermes_service"] = "unavailable"
    except Exception:
        services["hermes_service"] = "unavailable"

    # LDR service
    try:
        ldr = getattr(app.state, "ldr_service", None)
        if ldr:
            healthy = await ldr.health() if hasattr(ldr, 'health') else True
            services["ldr_service"] = "ready" if healthy else "degraded"
        else:
            services["ldr_service"] = "unavailable"
    except Exception:
        services["ldr_service"] = "unavailable"

    # Document service
    try:
        doc = getattr(app.state, "document_service", None)
        services["document_service"] = "ready" if doc else "unavailable"
    except Exception:
        services["document_service"] = "unavailable"

    # Agent delegator
    try:
        delegator = getattr(app.state, "agent_delegator", None)
        if delegator:
            agents = len(delegator.registry.divisions)
            services["agent_delegator"] = f"ready ({agents} divisions)"
        else:
            services["agent_delegator"] = "unavailable"
    except Exception:
        services["agent_delegator"] = "unavailable"

    all_ready = all(
        v.startswith("ready") if isinstance(v, str) else v
        for v in services.values()
    )
    return {
        "status": "ok" if all_ready else "degraded",
        "version": __version__,
        "services": services,
    }
