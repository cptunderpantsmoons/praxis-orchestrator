"""AgentMail webhook endpoint with Svix signature verification.

Handles the full AgentMail event taxonomy (10 event types) by delegating
payload parsing to :mod:`praxis.webhooks.payloads`. Only ``message.received*``
events are routed into the LangGraph; other events (sent / delivered / bounced
/ complained / rejected / domain.verified) are logged and acknowledged so the
agent can still react to delivery signals without blocking on them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import orjson
import structlog
from fastapi import APIRouter, HTTPException, Request, status

from praxis.config import get_settings
from praxis.models.schemas import AgentMetadata, InboundEmail, WebhookResponse
from praxis.webhooks.payloads import parse_agentmail_event
from praxis.webhooks.svix import verify_svix_signature

logger = structlog.get_logger()

router = APIRouter()


def _parse_inbound_email(payload: dict[str, Any]) -> InboundEmail:
    """Legacy fallback: normalize the old flat AgentMail payload shape.

    Real AgentMail events (v0.5.x) use a nested ``message`` / ``thread`` shape
    handled by ``parse_agentmail_event``. This helper remains for the
    smoke-test path and any old payloads that haven't been migrated.
    """
    data = payload.get("data", payload)
    from_raw = data.get("from", "")
    if isinstance(from_raw, dict):
        sender = from_raw.get("email", "")
    else:
        sender = str(from_raw)

    recipients_raw = data.get("to", [])
    recipients: list[str] = []
    if isinstance(recipients_raw, list):
        for item in recipients_raw:
            if isinstance(item, dict):
                recipients.append(item.get("email", ""))
            else:
                recipients.append(str(item))
    elif isinstance(recipients_raw, dict):
        recipients.append(recipients_raw.get("email", ""))

    return InboundEmail(
        message_id=data.get("id") or payload.get("id", ""),
        sender=sender,
        recipients=recipients,
        subject=data.get("subject", ""),
        body=data.get("body", ""),
        html_body=data.get("html_body"),
        received_at=datetime.now(UTC),
    )


@router.post("/email", status_code=status.HTTP_200_OK)
async def receive_email(request: Request) -> WebhookResponse:
    """Receive and verify an AgentMail webhook, then invoke the LangGraph.

    1. Read the raw body (needed for signature verification).
    2. Verify the Svix-compatible HMAC-SHA256 signature.
    3. Parse the payload with the new ``parse_agentmail_event``.
    4. Dispatch by event type:
       - ``message.received*`` → build ``InboundEmail``, invoke the graph
       - ``message.delivered`` / ``message.sent`` → log + 200 OK
       - ``message.bounced`` / ``message.complained`` / ``message.rejected``
         → log warning + 200 OK
       - ``domain.verified`` → log + 200 OK
       - unknown event_type → 200 OK, logged as ignored
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

    # ── New AgentMail event-shape parser (preferred) ─────────────
    parsed = parse_agentmail_event(payload)
    if parsed is None:
        # Unknown event_type or malformed payload — try the legacy fallback
        # so the smoke test (which uses the old flat shape) keeps working.
        event_type_raw = payload.get("event_type") or payload.get("type", "")
        if not event_type_raw:
            logger.warning(
                "webhook.unknown_payload",
                keys=list(payload.keys())[:10],
            )
            return WebhookResponse(
                status="ignored",
                event_id="",
                thread_id="",
                message="Payload shape unrecognised",
            )
        # Otherwise treat as a flat message payload (legacy / smoke test)
        email = _parse_inbound_email(payload)
        event_id = payload.get("id", "") or payload.get("event_id", "")
        thread_id = f"thread_{event_id}" if event_id else f"thread_{uuid4().hex}"
        logger.info(
            "webhook.legacy_payload",
            event_id=event_id,
            thread_id=thread_id,
        )
    else:
        event_id = parsed.event_id
        # Handle non-received events: log + 200 OK
        if not parsed.is_received():
            logger.info(
                "webhook.event_logged",
                event_type=parsed.event_type,
                event_id=event_id,
                bounce_type=parsed.bounce_type,
                complaint_type=parsed.complaint_type,
                reject_reason=parsed.reject_reason,
                domain_name=parsed.domain_name,
            )
            return WebhookResponse(
                status="logged",
                event_id=event_id,
                thread_id=parsed.thread_id or "",
                message=f"Event {parsed.event_type} acknowledged",
            )
        # Received event: build InboundEmail and invoke the graph
        email = parsed.to_inbound_email()
        if email is None:  # pragma: no cover — is_received() guarantees non-None
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Parsed received event has no email payload",
            )
        thread_id = (
            f"thread_{parsed.thread_id}"
            if parsed.thread_id
            else f"thread_{event_id or uuid4().hex}"
        )
        if parsed.is_spam:
            logger.info("webhook.spam_received", event_id=event_id, sender=email.sender)
        elif parsed.is_blocked:
            logger.info("webhook.blocked", event_id=event_id)
            return WebhookResponse(
                status="blocked",
                event_id=event_id,
                thread_id=thread_id,
                message="Message blocked",
            )
        elif parsed.is_unauthenticated:
            logger.warning(
                "webhook.unauthenticated",
                event_id=event_id,
                sender=email.sender,
            )

    # ── Invoke the graph for received (non-blocked) emails ──────
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        logger.error("webhook.graph_not_initialized")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Graph not initialized",
        )

    logger.info(
        "webhook.graph_invoke",
        event_id=event_id,
        thread_id=thread_id,
    )

    from praxis.graph.state import AgentState

    initial_state: AgentState = {
        "email_content": email,
        "metadata": AgentMetadata(thread_id=thread_id, workflow_id=event_id),
    }

    await graph.ainvoke(
        initial_state,
        config={
            "configurable": {
                "thread_id": thread_id,
                "router": request.app.state.umans_router,
            },
        },
    )

    return WebhookResponse(
        status="accepted",
        event_id=event_id,
        thread_id=thread_id,
        message="Email received and queued for processing",
    )
