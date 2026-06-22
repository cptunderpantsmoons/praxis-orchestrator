"""GET /admin/metrics, GET /admin/metrics/prom, POST /admin/metrics/reset.

These endpoints expose the in-process :class:`MetricsRegistry` to the TUI and
to Prometheus scrapers. JSON is the primary format for the TUI; the
``/metrics/prom`` endpoint emits Prometheus text format for scraping.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from praxis.features.metrics import get_metrics
from praxis.webhooks.auth import require_admin_token

router = APIRouter(prefix="/admin/metrics", tags=["admin"])


@router.get("")
async def get_metrics_snapshot(_: None = Depends(require_admin_token)) -> dict[str, Any]:
    """Return the full metrics registry as JSON."""
    return get_metrics().snapshot()


@router.get("/prom", response_class=PlainTextResponse)
async def get_metrics_prometheus(
    _: None = Depends(require_admin_token),
) -> str:
    """Return the metrics registry in Prometheus text exposition format."""
    return get_metrics().prometheus_text()


@router.post("/reset")
async def reset_metrics(_: None = Depends(require_admin_token)) -> dict[str, str]:
    """Zero all counters, gauges, and histograms."""
    get_metrics().reset()
    return {"status": "reset"}
