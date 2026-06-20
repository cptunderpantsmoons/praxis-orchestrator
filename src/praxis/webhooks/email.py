"""AgentMail webhook endpoint with Svix signature verification."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import orjson
import structlog
from fastapi import APIRouter, HTTPException, Request, status

from praxis.config import get_settings
from praxis.models.schemas import AgentMetadata, InboundEmail, WebhookResponse
from praxis.webhooks.svix import verify_svix_signature

logger = structlog.get_logger()

router = APIRouter()


def _parse_inbound_email(payload: dict[str, Any]) -> InboundEmail:
    """Normalize an AgentMail webhook payload into InboundEmail."""
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
    3. Parse the payload into InboundEmail.
    4. Initialize AgentState and invoke the compiled graph.
    5. Return 200 OK with event ID and LangGraph thread_id.
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

    # Normalize into canonical InboundEmail and invoke the graph
    email = _parse_inbound_email(payload)
    thread_id = f"thread_{event_id}" if event_id else f"thread_{uuid4().hex}"

    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        logger.error("webhook.graph_not_initialized")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Graph not initialized",
        )

    logger.info("webhook.graph_invoke", event_id=event_id, thread_id=thread_id, event_type=event_type)

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


# Import Any at the bottom to satisfy type check without circular issues
from typing import Any  # noqa: E402
