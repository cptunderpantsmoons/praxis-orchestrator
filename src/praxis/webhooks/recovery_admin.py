"""Admin endpoints for the auto-healing recovery agent.

All endpoints require the admin token. Exposes:
- ``GET /admin/recovery/events`` — recent recovery events (filter by status/pattern)
- ``GET /admin/recovery/stats`` — aggregate success/failure counts
- ``GET /admin/recovery/health`` — worker running status + config
- ``POST /admin/recovery/enable`` — start the worker
- ``POST /admin/recovery/disable`` — stop the worker
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from praxis.webhooks.auth import require_admin_token

router = APIRouter(prefix="/admin/recovery", tags=["admin"])


def _get_manager() -> Any:
    """Get the RecoveryManager from app.state. Raises 503 if not initialized."""
    from praxis.main import app

    mgr = getattr(app.state, "recovery_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Recovery manager not initialized",
        )
    return mgr


@router.get("/events")
async def list_recovery_events(
    _auth: None = Depends(require_admin_token),
    limit: int = Query(100, ge=1, le=1000),
    status_filter: str | None = Query(None, alias="status"),
    pattern: str | None = Query(None),
) -> dict[str, Any]:
    """List recent recovery events (failures + actions taken)."""
    mgr = _get_manager()
    events = mgr.list_events(limit=limit, status=status_filter, pattern=pattern)
    return {
        "count": len(events),
        "events": [e.model_dump(mode="json") for e in events],
    }


@router.get("/stats")
async def recovery_stats(
    _auth: None = Depends(require_admin_token),
) -> dict[str, Any]:
    """Aggregate success/failure counts per pattern and tool."""
    mgr = _get_manager()
    return mgr.stats().model_dump(mode="json")


@router.get("/health")
async def recovery_health(
    _auth: None = Depends(require_admin_token),
) -> dict[str, Any]:
    """Worker running status, queue size, last processed timestamp, config."""
    mgr = _get_manager()
    return mgr.health_status()


@router.post("/enable")
async def enable_recovery(
    _auth: None = Depends(require_admin_token),
) -> dict[str, Any]:
    """Start the recovery worker."""
    mgr = _get_manager()
    mgr.enable()
    return {"status": "enabled", "worker_running": True}


@router.post("/disable")
async def disable_recovery(
    _auth: None = Depends(require_admin_token),
) -> dict[str, Any]:
    """Stop the recovery worker."""
    mgr = _get_manager()
    mgr.disable()
    return {"status": "disabled", "worker_running": False}
