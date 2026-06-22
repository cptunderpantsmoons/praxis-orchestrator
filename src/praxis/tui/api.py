"""HTTP client for the PRAXIS admin API."""
from __future__ import annotations

from typing import Any

import httpx


class PraxisClient:
    """Async client for /admin/* endpoints."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, **params) -> Any:
        resp = await self._http.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, json: dict | None = None) -> Any:
        resp = await self._http.post(path, json=json)
        resp.raise_for_status()
        return resp.json()

    async def get_system(self) -> dict:
        return await self._get("/admin/system")

    async def get_settings(self) -> dict:
        return await self._get("/admin/settings")

    async def post_settings(self, patch: dict) -> dict:
        return await self._post("/admin/settings", json=patch)

    async def get_audit_logs(self, since: str | None = None, level: str | None = None, event: str | None = None, limit: int = 100) -> dict:
        params = {"limit": limit}
        if since:
            params["since"] = since
        if level:
            params["level"] = level
        if event:
            params["event"] = event
        return await self._get("/admin/logs/audit", **params)

    async def get_metrics(self) -> dict:
        return await self._get("/admin/metrics")

    async def get_metrics_prom(self) -> str:
        resp = await self._http.get("/admin/metrics/prom")
        resp.raise_for_status()
        return resp.text

    async def reset_metrics(self) -> dict:
        return await self._post("/admin/metrics/reset")

    async def get_failed_events(self) -> dict:
        return await self._get("/admin/failed-events")

    async def retry_failed(self, event_id: str) -> dict:
        return await self._post("/admin/retry-failed", json={"event_id": event_id})
