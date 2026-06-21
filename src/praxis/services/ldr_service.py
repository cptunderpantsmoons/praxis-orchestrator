"""LDR (Local Deep Research) service — async wrapper around the LDR HTTP API.

Provides multi-document synthesis and report generation via a running LDR
container.  The wrapper endpoint is deployed inside the LDR container and
exposes ``quick_summary`` and ``generate_report`` with simple API-key auth.

Concurrency note (REQ-310):
    LDR makes its own LLM calls to the Umans API, bypassing the praxis
    UmansConcurrencyRouter.  To avoid exceeding concurrency limits:
    * Default mode is ``quick`` (1 search + 1 synthesis = ~2 Umans calls).
    * ``full`` mode is capped at ``searches_per_section=1`` (~4 Umans calls).
    * Calls are run via ``asyncio.to_thread`` so they don't block the event loop.
    * The ReAct agent decides when to invoke research — it's not called on
      every email, only when the user's query warrants it.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from praxis.config import get_settings

logger = logging.getLogger(__name__)

# Default timeouts — LDR research can take 30-120s
_HEALTH_TIMEOUT = 10.0
_RESEARCH_TIMEOUT = 180.0


class LdrService:
    """Async client for the LDR HTTP wrapper.

    Parameters
    ----------
    base_url : str
        URL of the LDR wrapper (e.g. ``http://172.16.3.3:5001``).
    api_key : str
        API key configured in the wrapper.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (
            base_url
            or getattr(settings, "ldr_url", None)
            or "http://127.0.0.1:5001"
        ).rstrip("/")
        self.api_key = (
            api_key
            or getattr(settings, "ldr_api_key", None)
            or "praxis_ldr_2026"
        )
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=_RESEARCH_TIMEOUT)
        return self._client

    async def health(self) -> bool:
        """Check if the LDR wrapper is reachable."""
        try:
            client = await self._ensure_client()
            resp = await client.get(
                f"{self.base_url}/health",
                timeout=_HEALTH_TIMEOUT,
            )
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("ldr.health_failed: %s", exc)
            return False

    async def research(
        self,
        query: str,
        mode: str = "quick",
        searches_per_section: int = 1,
    ) -> dict[str, Any]:
        """Run a research query and return the result.

        Parameters
        ----------
        query : str
            The research question.
        mode : str
            ``quick`` (1 search + 1 synthesis, ~2 Umans calls) or
            ``full`` (multi-section report, ~4 Umans calls with
            ``searches_per_section=1``).
        searches_per_section : int
            Only used in ``full`` mode.  Capped at 2 to limit concurrency.

        Returns
        -------
        dict
            LDR response with keys: ``summary``, ``findings``,
            ``sources``, ``iterations``, ``questions``.
        """
        client = await self._ensure_client()

        # Cap concurrency — never allow more than 2 searches per section
        searches_per_section = min(int(searches_per_section), 2)

        payload = {
            "query": query,
            "api_key": self.api_key,
            "mode": mode,
            "searches_per_section": searches_per_section,
        }

        logger.info(
            "ldr.research_start: query=%s mode=%s searches_per_section=%s",
            query[:100], mode, searches_per_section,
        )

        # Use async HTTP directly — httpx.AsyncClient is already non-blocking
        resp = await client.post(
            f"{self.base_url}/research",
            json=payload,
            timeout=_RESEARCH_TIMEOUT,
        )
        resp.raise_for_status()

        data = resp.json()
        result = data.get("result", {})

        # Extract key fields for logging
        summary = result.get("summary", "")
        sources = result.get("sources", [])
        findings = result.get("findings", [])

        logger.info(
            "ldr.research_complete: query=%s mode=%s summary_chars=%d sources=%d findings=%d",
            query[:100], mode, len(summary), len(sources), len(findings),
        )

        return result

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
