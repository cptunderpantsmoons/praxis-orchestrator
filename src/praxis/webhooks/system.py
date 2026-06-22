"""GET /admin/system — health snapshot.

Returns a quick snapshot of router/checkpointer/services status for the TUI
dashboard. Full per-service health checks are out of scope here — use ``/health``
for that — this endpoint only reports whether each service is *configured* and
exposes the current router concurrency state.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from praxis.config import get_settings
from praxis.webhooks.auth import require_admin_token

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/system")
async def system_status(_: None = Depends(require_admin_token)) -> dict[str, Any]:
    """Return a health snapshot for the admin dashboard."""
    settings = get_settings()
    return {
        "router": _router_snapshot(),
        "checkpointer": _checkpointer_snapshot(),
        "services": _services_snapshot(),
        "environment": settings.environment,
        "tool_protocol": settings.tool_protocol,
    }


def _router_snapshot() -> dict[str, Any]:
    """Best-effort snapshot of the UmansConcurrencyRouter state.

    Returns per-model breakdowns so the TUI dashboard's ``router`` panel
    (which reads ``router["active"]``, ``router["peak"]``,
    ``router["limits"]``) renders correctly. Also includes the global
    active/peak counts for completeness.

    Looks for a router on ``app.state`` first (production path). If absent
    (e.g. during unit tests with no lifespan), constructs a transient router
    just to read the configured limits.
    """
    try:
        from praxis.main import app
        from praxis.router import UmansConcurrencyRouter

        router_obj = getattr(app.state, "umans_router", None)
        if router_obj is None:
            router_obj = UmansConcurrencyRouter()
        # Per-model breakdowns (dicts of model_name -> count). The router
        # stores these as private attrs; fall back to empty dicts if the
        # attrs are missing (e.g. a mock router in tests).
        active = dict(getattr(router_obj, "_active", {}) or {})
        peak = dict(getattr(router_obj, "_peak", {}) or {})
        limits = dict(getattr(router_obj, "limits", {}) or {})
        return {
            "active": active,
            "peak": peak,
            "limits": limits,
            # Global totals (kept for backwards-compat / other consumers).
            "active_global": getattr(router_obj, "_global_active", 0),
            "peak_global": getattr(router_obj, "_global_peak", 0),
        }
    except Exception as exc:  # pragma: no cover — defensive
        return {"error": str(exc)}


def _checkpointer_snapshot() -> dict[str, Any]:
    """Report the checkpointer type currently in use."""
    try:
        from praxis.main import app

        cp = getattr(app.state, "checkpointer", None)
        return {"type": type(cp).__name__ if cp is not None else "none"}
    except Exception as exc:  # pragma: no cover — defensive
        return {"error": str(exc)}


def _services_snapshot() -> dict[str, Any]:
    """Report configured-ness of each external service.

    Full liveness probing belongs to ``/health``; here we only report whether
    the relevant env var has been set, so the TUI can show "configured" vs.
    "missing config" badges without paying for network round-trips.
    """
    from praxis.features.metrics import get_metrics

    snap = get_metrics().snapshot()
    settings = get_settings()
    field_map = {
        "neo4j": "neo4j_uri",
        "qdrant": "qdrant_url",
        "postgres": "postgres_dsn",
        "hermes": "hermes_base_url",
        "ldr": "ldr_url",
        "agentmail": "agentmail_api_key",
    }
    services: dict[str, Any] = {}
    for name, field in field_map.items():
        value = getattr(settings, field, "")
        services[name] = {"configured": bool(value)}
    services["metrics_counters"] = snap["counters"]
    services["metrics_gauges"] = snap["gauges"]
    return services
