"""Bearer-token authentication dependency for /admin/* endpoints."""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from praxis.config import get_settings

# Minimum length for PRAXIS_ADMIN_TOKEN in production. The spec requires
# >= 32 chars to prevent brute-force attacks. Only enforced in production
# so dev/test ergonomics are preserved (tests use short tokens).
_MIN_TOKEN_LENGTH = 32


async def require_admin_token(authorization: str | None = Header(None)) -> None:
    """Validate the bearer token against PRAXIS_ADMIN_TOKEN.

    Raises:
        HTTPException(500): PRAXIS_ADMIN_TOKEN not configured, or (in
            production) configured but shorter than 32 characters.
        HTTPException(401): No Authorization header or wrong scheme.
        HTTPException(403): Token present but invalid.
    """
    settings = get_settings()
    expected = settings.praxis_admin_token
    if not expected:
        raise HTTPException(status_code=500, detail="PRAXIS_ADMIN_TOKEN not configured")
    # Important #8: enforce the 32-char minimum in production only. A
    # short token in production is a misconfiguration that could allow
    # brute-force attacks — fail closed with 500 rather than accepting
    # the weak token. In development/test, short tokens are allowed so
    # test ergonomics are preserved.
    if (
        settings.environment == "production"
        and len(expected) < _MIN_TOKEN_LENGTH
    ):
        raise HTTPException(
            status_code=500,
            detail=(
                f"PRAXIS_ADMIN_TOKEN must be >= {_MIN_TOKEN_LENGTH} chars "
                f"in production (got {len(expected)})"
            ),
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    provided = authorization[len("Bearer "):]
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Unauthorized")
