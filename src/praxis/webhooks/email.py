"""AgentMail webhook endpoint for inbound email ingestion.

Exposes ``POST /webhook/email`` which:

1.  Reads the raw request body.
2.  Extracts ``svix-id``, ``svix-timestamp``, ``svix-signature`` headers.
3.  Verifies the Svix-compatible HMAC-SHA256 signature.
4.  Parses the payload into :class:`InboundEmailPayload`.
5.  Returns ``200 OK`` with the event ID.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from praxis.config import Settings, get_settings
from praxis.models.schemas import InboundEmailPayload, WebhookResponse
from praxis.webhooks.svix import verify_webhook

router = APIRouter(tags=["webhooks"])

# Type alias for settings dependency (avoids B008: Depends in default position)
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post("/email", response_model=WebhookResponse)
async def receive_email(
    request: Request,
    settings: SettingsDep,
    svix_id: str | None = Header(default=None, alias="svix-id"),
    svix_timestamp: str | None = Header(default=None, alias="svix-timestamp"),
    svix_signature: str | None = Header(default=None, alias="svix-signature"),
) -> WebhookResponse:
    """Receive and verify an inbound email webhook from AgentMail.

    Verifies the Svix-compatible signature before processing the payload.
    Returns ``200 OK`` with the event ID on success.
    """
    body = await request.body()

    # ── Validate required headers ───────────────────────────────────
    if not all([svix_id, svix_timestamp, svix_signature]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing required Svix headers (svix-id, svix-timestamp, svix-signature)",
        )

    # ── Verify signature ────────────────────────────────────────────
    secret = settings.svix_webhook_secret.get_secret_value()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook secret not configured",
        )

    if not verify_webhook(body, svix_id, svix_timestamp, svix_signature, secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    # ── Parse payload ───────────────────────────────────────────────
    try:
        payload = InboundEmailPayload.model_validate_json(body)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid payload: {exc}",
        ) from exc

    return WebhookResponse(event_id=payload.id)
