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
from praxis.features.metrics import get_metrics
from praxis.models.umans import UMANS_MODELS, get_model_config

logger = structlog.get_logger()


class UmansConcurrencyRouter:
    """Concurrency-limited router for Umans inference API calls.

    Each model family gets its own asyncio.Semaphore (capped at 4).
    A GLOBAL semaphore of 4 gates ALL calls across all families — no more
    than 4 inference calls may be in flight at any time.

    The router exposes ``invoke()`` for chat-completion requests and
    ``acquire()`` for callers that need to manage the HTTP request
    themselves (e.g. streaming).
    """

    # Hard global limit — no more than this many concurrent Umans API calls total
    GLOBAL_CONCURRENCY_LIMIT = 4

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = http_client  # injectable for testing
        self._owns_client: bool = http_client is None  # we own it if we create it

        # Global semaphore: hard cap across all families
        self._global_semaphore = asyncio.Semaphore(self.GLOBAL_CONCURRENCY_LIMIT)

        # One semaphore per model, keyed by model name
        self._semaphores: dict[str, asyncio.Semaphore] = {
            name: asyncio.Semaphore(config.concurrency_limit)
            for name, config in UMANS_MODELS.items()
        }

        # Instrumentation: track active and peak concurrency
        self._active: dict[str, int] = {name: 0 for name in UMANS_MODELS}
        self._peak: dict[str, int] = {name: 0 for name in UMANS_MODELS}
        self._global_active: int = 0
        self._global_peak: int = 0

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

        # Acquire global first, then per-model — prevents family stacking
        async with self._global_semaphore:
            self._global_active += 1
            self._global_peak = max(self._global_peak, self._global_active)
            try:
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
            finally:
                self._global_active -= 1

    async def embed(
        self,
        model_name: str,
        input_text: str,
        **kwargs: Any,
    ) -> list[float]:
        """Generate an embedding vector, respecting the model's semaphore.

        Unlike :meth:`invoke`, this returns the raw embedding vector rather
        than a chat-completion response. It is intended for embedding models
        registered with ``family="embed"`` (e.g. ``umans-embed-small``).

        Args:
            model_name: An embedding model name (must be registered).
            input_text: The text to embed.
            **kwargs: Extra payload fields (e.g. ``encoding_format``).

        Returns:
            A list of floats representing the embedding.

        Raises:
            ValueError: If model_name is not registered.
            httpx.HTTPStatusError: On non-2xx API responses.
        """
        config = get_model_config(model_name)
        if config.family != "embed":
            msg = (
                f"Model {model_name!r} is not an embedding model "
                f"(family={config.family!r}). Use invoke() for chat models."
            )
            raise ValueError(msg)
        semaphore = self._semaphores[model_name]

        async with self._global_semaphore:
            self._global_active += 1
            self._global_peak = max(self._global_peak, self._global_active)
            try:
                async with semaphore:
                    self._active[model_name] += 1
                    self._peak[model_name] = max(self._peak[model_name], self._active[model_name])
                    try:
                        start = time.monotonic()
                        vector = await self._call_embed(model_name, input_text, **kwargs)
                        elapsed_ms = (time.monotonic() - start) * 1000
                        logger.info(
                            "umans.embed",
                            model=model_name,
                            family=config.family,
                            elapsed_ms=round(elapsed_ms, 2),
                            vector_dim=len(vector),
                        )
                        return vector
                    finally:
                        self._active[model_name] -= 1
            finally:
                self._global_active -= 1

    @asynccontextmanager
    async def acquire(self, model_name: str) -> AsyncIterator[None]:
        """Acquire a concurrency slot without making an API call.

        Useful when the caller wants to manage the HTTP request itself
        (e.g. streaming) while still respecting both the global and per-model
        semaphores.
        """
        if model_name not in self._semaphores:
            raise ValueError(f"Unknown model: {model_name!r}")
        metrics = get_metrics()
        metrics.increment_gauge(f"router_active:{model_name}")
        metrics.increment_gauge("router_global_active")
        # Update peak gauges (only when current exceeds the recorded peak)
        gauges = metrics.snapshot()["gauges"]
        current = gauges.get(f"router_active:{model_name}", 0)
        peak = gauges.get(f"router_peak:{model_name}", 0)
        if current > peak:
            metrics.set_gauge(f"router_peak:{model_name}", current)
        global_current = gauges.get("router_global_active", 0)
        global_peak = gauges.get("router_global_peak", 0)
        if global_current > global_peak:
            metrics.set_gauge("router_global_peak", global_current)
        try:
            async with self._global_semaphore:
                async with self._semaphores[model_name]:
                    yield
        finally:
            metrics.decrement_gauge(f"router_active:{model_name}")
            metrics.decrement_gauge("router_global_active")

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
    def global_active(self) -> int:
        """Current number of in-flight requests across ALL models."""
        return self._global_active

    @property
    def global_peak(self) -> int:
        """Peak concurrent requests observed across ALL models."""
        return self._global_peak

    @property
    def limits(self) -> dict[str, int]:
        """Per-family concurrency limits (for diagnostics)."""
        families: dict[str, int] = {}
        for config in UMANS_MODELS.values():
            families[config.family] = config.concurrency_limit
        families["global"] = self.GLOBAL_CONCURRENCY_LIMIT
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

    async def _call_embed(
        self,
        model_name: str,
        input_text: str,
        **kwargs: Any,
    ) -> list[float]:
        """Make the actual HTTP call to the Umans embeddings endpoint."""
        client = await self._get_client()
        payload: dict[str, Any] = {
            "model": model_name,
            "input": input_text,
            **kwargs,
        }
        base = str(client.base_url).rstrip("/")
        url = (
            "/embeddings"
            if base
            else (f"{self._settings.umans_base_url.rstrip('/')}/embeddings")
        )
        resp = await client.post(
            url,
            content=orjson.dumps(payload),
        )
        resp.raise_for_status()
        body = orjson.loads(resp.content)
        # OpenAI-compatible: {"data": [{"embedding": [...]}]}
        data = body.get("data") or []
        if not data:
            msg = f"Umans embeddings endpoint returned no data: {body}"
            raise ValueError(msg)
        embedding = data[0].get("embedding") or []
        if not embedding:
            msg = f"Umans embeddings endpoint returned empty vector: {body}"
            raise ValueError(msg)
        return [float(x) for x in embedding]


__all__ = ["UmansConcurrencyRouter"]
