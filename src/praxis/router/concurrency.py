"""Umans Concurrency Router — enforces per-model-family semaphore limits.

Uses native ``asyncio.Semaphore`` to guarantee that in-flight requests never
exceed the configured threshold for each Umans model family:

- Kimi (``umans-coder``):          4 concurrent calls
- GLM  (``umans-glm-5.2``):        4 concurrent calls
- Qwen (``umans-flash``):          8 concurrent calls

When a semaphore is at capacity, incoming coroutines naturally queue within
the event loop rather than blocking threads or spawning processes.  This
ensures predictable backpressure and zero rate-limit violations.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Any

import httpx


class ModelFamily(StrEnum):
    """Umans model families with distinct concurrency limits."""

    KIMI = "kimi"
    GLM = "glm"
    QWEN = "qwen"


# ── Model-name → family mapping ──────────────────────────────────────
MODEL_FAMILY_MAP: dict[str, ModelFamily] = {
    "umans-coder": ModelFamily.KIMI,
    "umans-flash": ModelFamily.QWEN,
    "umans-glm-5.2": ModelFamily.GLM,
}

# ── Default per-family semaphore limits ──────────────────────────────
FAMILY_LIMITS: dict[ModelFamily, int] = {
    ModelFamily.KIMI: 4,
    ModelFamily.GLM: 4,
    ModelFamily.QWEN: 8,
}


class UmansConcurrencyRouter:
    """Manages concurrency limits for Umans API inference calls.

    All coroutines requesting inference must acquire the per-family
    semaphore before initiating an HTTP connection, guaranteeing that
    in-flight requests never exceed the configured threshold.

    Example::

        router = UmansConcurrencyRouter(
            base_url="https://api.umans.ai",
            api_key="sk-...",
        )
        response = await router.invoke(
            model="umans-flash",
            messages=[{"role": "user", "content": "Hello"}],
        )
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        kimi_limit: int = 4,
        glm_limit: int = 4,
        qwen_limit: int = 8,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

        family_limits: dict[ModelFamily, int] = {
            ModelFamily.KIMI: kimi_limit,
            ModelFamily.GLM: glm_limit,
            ModelFamily.QWEN: qwen_limit,
        }

        self._semaphores: dict[ModelFamily, asyncio.Semaphore] = {
            family: asyncio.Semaphore(limit) for family, limit in family_limits.items()
        }
        self._limits = family_limits
        # Track in-flight count per family (safe in single-threaded asyncio)
        self._active_counts: dict[ModelFamily, int] = {
            family: 0 for family in ModelFamily
        }
        self._client = client
        self._owns_client = client is None

    # ── Public API ───────────────────────────────────────────────────

    @staticmethod
    def get_family(model: str) -> ModelFamily:
        """Return the model family for a given model name.

        Raises:
            ValueError: If the model name is not recognised.
        """
        if model not in MODEL_FAMILY_MAP:
            raise ValueError(
                f"Unknown model: {model!r}. "
                f"Known models: {list(MODEL_FAMILY_MAP)}"
            )
        return MODEL_FAMILY_MAP[model]

    async def invoke(
        self,
        model: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Route a chat-completion request through the concurrency router.

        Acquires the per-family semaphore before making the API call,
        ensuring concurrency limits are strictly enforced.

        Args:
            model: Umans model name (e.g. ``"umans-flash"``).
            messages: OpenAI-compatible message list.
            **kwargs: Additional parameters forwarded to the API (e.g.
                ``temperature``, ``max_tokens``, ``response_format``).

        Returns:
            Parsed JSON response from the Umans API.
        """
        family = self.get_family(model)
        semaphore = self._semaphores[family]

        async with semaphore:
            self._active_counts[family] += 1
            try:
                return await self._call_api(model, messages, **kwargs)
            finally:
                self._active_counts[family] -= 1

    @property
    def active_counts(self) -> dict[str, int]:
        """Current in-flight request count per family."""
        return {family.value: count for family, count in self._active_counts.items()}

    @property
    def limits(self) -> dict[str, int]:
        """Configured concurrency limits per family."""
        return {family.value: limit for family, limit in self._limits.items()}

    async def close(self) -> None:
        """Close the HTTP client if owned by this router."""
        if self._owns_client and self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    # ── Internal ─────────────────────────────────────────────────────

    async def _call_api(
        self,
        model: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make the actual HTTP call to the Umans API.

        Override or monkey-patch this method in tests to inject mock behaviour
        without touching real network resources.
        """
        client = await self._get_client()
        response = await client.post(
            f"{self._base_url}/v1/chat/completions",
            json={"model": model, "messages": messages, **kwargs},
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        response.raise_for_status()
        return response.json()

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create the HTTP client if not injected."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client
