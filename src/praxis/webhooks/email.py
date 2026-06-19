"""AgentMail webhook endpoint with Svix signature verification."""

from __future__ import annotations

import orjson
import structlog
from fastapi import APIRouter, HTTPException, Request, status

from praxis.config import get_settings
from praxis.webhooks.svix import verify_svix_signature

logger = structlog.get_logger()

router = APIRouter()


@router.post("/email", status_code=status.HTTP_200_OK)
async def receive_email(request: Request) -> dict:
    """Receive and verify an AgentMail webhook.

    1. Read the raw body (needed for signature verification).
    2. Verify the Svix-compatible HMAC-SHA256 signature.
    3. Parse the payload and extract the event ID.
    4. Return 200 OK with the event ID.

    In Phase 2, this handler will initialize the LangGraph state
    and invoke the compiled graph.
    """
    raw_body = await request.body()
    settings = get_settings()

    # Extract Svix headers
    svix_id = request.headers.get("svix-id", "")
    svix_timestamp = request.headers.get("svix-timestamp", "")
    svix_signature = request.headers.get("svix-signature", "")

    # Verify required headers are present
    if not svix_id or not svix_timestamp or not svix_signature:
        logger.warning("webhook.missing_headers", has_id=bool(svix_id))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Svix signature headers",
        )

    # Verify signature
    if not verify_svix_signature(
        raw_body=raw_body,
        svix_id=svix_id,
        svix_timestamp=svix_timestamp,
        svix_signature=svix_signature,
        secret=settings.agentmail_webhook_secret,
    ):
        logger.warning("webhook.invalid_signature", svix_id=svix_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    # Parse payload
    try:
        payload = orjson.loads(raw_body)
    except orjson.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from e

    event_id = payload.get("id", "")
    event_type = payload.get("type", "email.received")

    logger.info("webhook.received", event_id=event_id, event_type=event_type)

    return {
        "status": "accepted",
        "event_id": event_id,
        "message": "Email received and verified",
    }
