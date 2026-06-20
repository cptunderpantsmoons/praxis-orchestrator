"""Async Hermes client for prompt templating and structured output.

Wraps HTTP requests to the Hermes platform with retry, timeout, and
correlation-ID tracking.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()


class HermesAPIError(Exception):
    """Raised when the Hermes platform returns an error response."""


class HermesTimeoutError(HermesAPIError):
    """Raised when a Hermes request times out."""


class HermesRateLimitError(HermesAPIError):
    """Raised when the Hermes platform returns 429."""


class HermesClient:
    """Async client for the Hermes platform.

    Handles prompt rendering, structured output, and streaming.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8787",
        api_key: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_base_delay: float = 0.5,
        retry_max_delay: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay

    # ── Public API ────────────────────────────────────────────

    async def call_prompt(self, template_id: str, context: dict) -> dict:
        """Render a template and return the LLM response as a dict."""
        logger.info("hermes.call_prompt", template_id=template_id)
        payload = {"template_id": template_id, "context": context}
        resp = await self._request("POST", "/prompts/call", json=payload)
        return self._parse_response(resp)

    async def get_template(self, template_id: str) -> str:
        """Retrieve a template string by ID.

        The template_id is URL-encoded before being interpolated into the
        path, so callers may pass any string without breaking the request.
        """
        from urllib.parse import quote
        logger.info("hermes.get_template", template_id=template_id)
        encoded = quote(template_id, safe="")
        resp = await self._request("GET", f"/prompts/templates/{encoded}")
        data = resp.json()
        template = data.get("template", "")
        if not isinstance(template, str):
            raise HermesAPIError(
                f"Hermes returned non-string template: {type(template).__name__}"
            )
        return template

    async def stream_prompt(
        self, template_id: str, context: dict
    ) -> AsyncIterator[str]:
        """Stream an LLM response token by token."""
        logger.info("hermes.stream_prompt", template_id=template_id)
        payload = {"template_id": template_id, "context": context, "stream": True}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", f"{self.base_url}/prompts/stream", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        yield line[6:]

    async def get_structured_output(
        self, template_id: str, context: dict, schema: type
    ) -> dict:
        """Call template and validate structured output against a Pydantic schema."""
        logger.info(
            "hermes.get_structured_output", template_id=template_id, schema=schema.__name__
        )
        payload = {
            "template_id": template_id,
            "context": context,
            "response_format": {"type": "json_schema", "json_schema": {"name": schema.__name__}},
        }
        raw = await self._request("POST", "/prompts/structured", json=payload)
        data = self._parse_response(raw)
        # Validate
        validated = schema.model_validate(data)
        return validated.model_dump()

    # ── Internal helpers ──────────────────────────────────────

    async def _request(
        self, method: str, path: str, json: dict | None = None
    ) -> httpx.Response:
        """Make an HTTP request with retry logic."""
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.request(method, url, json=json, headers=headers)

                    if resp.status_code == 429:
                        delay = min(
                            self.retry_base_delay * (2 ** attempt),
                            self.retry_max_delay,
                        )
                        logger.warning("hermes.rate_limit", attempt=attempt + 1, retry_after=delay)
                        await self._sleep(delay)
                        continue

                    if resp.status_code >= 400:
                        raise HermesAPIError(
                            f"Hermes API error {resp.status_code}: {resp.text}"
                        )

                    return resp

            except httpx.TimeoutException:
                last_error = HermesTimeoutError(f"Request to {url} timed out")
            except httpx.ConnectError as exc:
                last_error = HermesAPIError(f"Cannot connect to Hermes: {exc}")

            if attempt < self.max_retries:
                delay = min(
                    self.retry_base_delay * (2 ** attempt),
                    self.retry_max_delay,
                )
                logger.info("hermes.retry", attempt=attempt + 1, delay=delay)
                await self._sleep(delay)

        raise last_error or HermesAPIError("Request failed")

    @staticmethod
    def _parse_response(resp: httpx.Response) -> dict:
        """Parse JSON response, handling common errors."""
        try:
            return resp.json()
        except Exception as exc:
            raise HermesAPIError(f"Invalid JSON response: {resp.text[:200]}") from exc

    @staticmethod
    async def _sleep(delay: float) -> None:
        """Non-blocking sleep (exponential backoff between retry attempts)."""
        await asyncio.sleep(delay)


async def get_hermes_client(settings: Any | None = None) -> HermesClient:
    """Factory to create a HermesClient from settings.

    Args:
        settings: Optional Settings instance.

    Returns:
        Configured HermesClient.
    """
    if settings is None:
        from praxis.config import get_settings
        settings = get_settings()

    return HermesClient(
        base_url=settings.hermes_base_url or "http://localhost:8787",
        api_key=settings.hermes_api_key,
        timeout=settings.hermes_timeout or 30.0,
        max_retries=settings.hermes_max_retries or 3,
    )
