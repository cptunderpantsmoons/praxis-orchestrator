"""FastAPI application entry point for PRAXIS v2.0.

Run with::

    uv run uvicorn praxis.main:app --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from praxis.config import get_settings
from praxis.models.schemas import HealthResponse
from praxis.router.concurrency import UmansConcurrencyRouter
from praxis.webhooks.email import router as webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle — create and cleanup the concurrency router."""
    settings = get_settings()

    router = UmansConcurrencyRouter(
        base_url=settings.umans_api_base_url,
        api_key=settings.umans_api_key.get_secret_value(),
        kimi_limit=settings.kimi_concurrency_limit,
        glm_limit=settings.glm_concurrency_limit,
        qwen_limit=settings.qwen_concurrency_limit,
    )
    app.state.umans_router = router

    yield

    await router.close()


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Native asynchronous enterprise email agent",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint.

    Returns the application status and version.  Sub-service statuses are
    ``"ready"`` for the API and ``"pending"`` for infrastructure that will
    be connected in later phases.
    """
    return HealthResponse(version=settings.app_version)


app.include_router(webhook_router, prefix="/webhook")
