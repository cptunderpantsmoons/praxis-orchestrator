"""Umans Concurrency Router.

Enforces per-model-family concurrency limits using native asyncio.Semaphore.
All inference requests route through this router to prevent API quota
exhaustion and ensure predictable latency under concurrent load.

When the semaphore limit is reached, additional coroutines queue in the
event loop (backpressure) rather than spawning processes or blocking threads.

Semaphore limits:
    Kimi family (umans-coder)     -> 4 concurrent calls
    GLM  family (umans-glm-5.2)  -> 4 concurrent calls
    Qwen family (umans-flash)     -> 8 concurrent calls
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import orjson
import structlog

from praxis.config import Settings, get_settings
from praxis.models.umans import UMANS_MODELS, get_model_config

logger = structlog.get_logger()


class UmansConcurrencyRouter:
    """Concurrency-limited router for Umans inference API calls.

    Each model family gets its own asyncio.Semaphore. The router exposes
    ``invoke()`` for chat-completion requests and ``acquire()`` for callers
    that need to manage the HTTP request themselves (e.g. streaming).
    """

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = http_client  # injectable for testing
        self._owns_client: bool = http_client is None  # we own it if we create it

        # One semaphore per model, keyed by model name
        self._semaphores: dict[str, asyncio.Semaphore] = {
            name: asyncio.Semaphore(config.concurrency_limit)
            for name, config in UMANS_MODELS.items()
        }

        # Instrumentation: track active and peak concurrency
        self._active: dict[str, int] = {name: 0 for name in UMANS_MODELS}
        self._peak: dict[str, int] = {name: 0 for name in UMANS_MODELS}

    # ── Public API ────────────────────────────────────────────────

    async def invoke(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a chat-completion request, respecting the model's semaphore.

        Args:
            model_name: One of the keys in ``UMANS_MODELS``.
            messages: OpenAI-format message list.
            **kwargs: Extra payload fields (max_tokens, temperature, etc.).

        Returns:
            Parsed JSON response from the Umans API.

        Raises:
            ValueError: If model_name is not registered.
            httpx.HTTPStatusError: On non-2xx API responses.
        """
        config = get_model_config(model_name)
        semaphore = self._semaphores[model_name]

        async with semaphore:
            self._active[model_name] += 1
            self._peak[model_name] = max(self._peak[model_name], self._active[model_name])
            try:
                start = time.monotonic()
                response = await self._call_api(model_name, messages, **kwargs)
                elapsed_ms = (time.monotonic() - start) * 1000
                logger.info(
                    "umans.invoke",
                    model=model_name,
                    family=config.family,
                    elapsed_ms=round(elapsed_ms, 2),
                )
                return response
            finally:
                self._active[model_name] -= 1

    @asynccontextmanager
    async def acquire(self, model_name: str) -> AsyncIterator[None]:
        """Acquire a concurrency slot without making an API call.

        Useful when the caller wants to manage the HTTP request itself
        (e.g. streaming) while still respecting the semaphore.
        """
        if model_name not in self._semaphores:
            raise ValueError(f"Unknown model: {model_name!r}")
        async with self._semaphores[model_name]:
            yield

    async def close(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client and self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    # ── Instrumentation ───────────────────────────────────────────

    def get_active(self, model_name: str) -> int:
        """Current number of in-flight requests for this model."""
        return self._active.get(model_name, 0)

    def get_peak(self, model_name: str) -> int:
        """Peak (maximum) concurrent requests observed for this model."""
        return self._peak.get(model_name, 0)

    @property
    def limits(self) -> dict[str, int]:
        """Per-family concurrency limits (for diagnostics)."""
        families: dict[str, int] = {}
        for config in UMANS_MODELS.values():
            families[config.family] = config.concurrency_limit
        return families

    # ── Internals ─────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the HTTP client, creating one if not injected."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._settings.umans_base_url,
                timeout=httpx.Timeout(30.0, connect=10.0),
                headers={
                    "Authorization": f"Bearer {self._settings.umans_api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def _call_api(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make the actual HTTP call to the Umans chat-completions endpoint."""
        client = await self._get_client()
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            **kwargs,
        }
        # Owned clients have base_url set; injected mock clients may not.
        # bool(httpx.URL('')) is True, so use str() to detect empty base_url.
        base = str(client.base_url).rstrip("/")
        url = (
            "/chat/completions"
            if base
            else (f"{self._settings.umans_base_url.rstrip('/')}/chat/completions")
        )
        resp = await client.post(
            url,
            content=orjson.dumps(payload),
        )
        resp.raise_for_status()
        return orjson.loads(resp.content)


__all__ = ["UmansConcurrencyRouter"]
