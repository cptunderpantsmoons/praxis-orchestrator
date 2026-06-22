"""Bearer-token authentication dependency for /admin/* endpoints."""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from praxis.config import get_settings


async def require_admin_token(authorization: str | None = Header(None)) -> None:
    """Validate the bearer token against PRAXIS_ADMIN_TOKEN.

    Raises:
        HTTPException(500): PRAXIS_ADMIN_TOKEN not configured.
        HTTPException(401): No Authorization header or wrong scheme.
        HTTPException(403): Token present but invalid.
    """
    settings = get_settings()
    expected = settings.praxis_admin_token
    if not expected:
        raise HTTPException(status_code=500, detail="PRAXIS_ADMIN_TOKEN not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    provided = authorization[len("Bearer "):]
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Unauthorized")
