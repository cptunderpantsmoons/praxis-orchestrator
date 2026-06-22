"""AgentMail webhook endpoint with Svix signature verification.

Handles the full AgentMail event taxonomy (10 event types) by delegating
payload parsing to :mod:`praxis.webhooks.payloads`. Only ``message.received*``
events are routed into the LangGraph; other events (sent / delivered / bounced
/ complained / rejected / domain.verified) are logged and acknowledged so the
agent can still react to delivery signals without blocking on them.

Robustness features:
- Webhook failures are caught, persisted to ``FAILED_EVENTS_PATH``, and
  acknowledged (HTTP 200) so AgentMail does not endlessly retry.
- Attachments are auto-downloaded to ``ATTACHMENTS_DIR`` and their local paths
  are surfaced to the ReAct agent.
- The ReAct agent is explicitly instructed to analyze every attachment that
  can be opened with :func:`analyze_document`.
"""

from __future__ import annotations

import os
import pathlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import orjson
import structlog
from fastapi import APIRouter, HTTPException, Request, status

from praxis.config import Settings, get_settings
from praxis.models.schemas import AgentMetadata, EmailAttachment, InboundEmail, WebhookResponse
from praxis.services.agent_registry import detect_region
from praxis.webhooks.payloads import parse_agentmail_event
from praxis.webhooks.svix import verify_svix_signature

logger = structlog.get_logger()

router = APIRouter()

DEFAULT_ATTACHMENTS_DIR = "/tmp/praxis_documents"
DEFAULT_FAILED_EVENTS_PATH = "/tmp/praxis_failed_events.jsonl"


class AttachmentDownloader:
    """Download email attachments from the AgentMail API to local disk.

    The downloader is intentionally tolerant: individual attachment failures are
    logged, but the webhook continues processing the email.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.agentmail.to",
        attachments_dir: str = DEFAULT_ATTACHMENTS_DIR,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._attachments_dir = pathlib.Path(attachments_dir)
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def download_all(
        self,
        attachments: list[EmailAttachment],
        inbox_id: str,
        message_id: str,
    ) -> list[EmailAttachment]:
        """Download each attachment and update ``local_path``.

        Args:
            attachments: Attachments parsed from the webhook payload.
            inbox_id: The inbox that received the email (AgentMail uses the
                inbox email address as its identifier).
            message_id: The message that owns the attachments.

        Returns:
            The input list with ``local_path`` populated for each download
            that succeeded.
        """
        if not attachments:
            return []

        self._attachments_dir.mkdir(parents=True, exist_ok=True)
        results: list[EmailAttachment] = []
        for attachment in attachments:
            if not attachment.attachment_id:
                logger.warning(
                    "attachment.skip_no_id",
                    filename=attachment.filename,
                    message_id=message_id,
                )
                results.append(attachment)
                continue
            try:
                local_path = await self._download_one(
                    inbox_id=inbox_id,
                    message_id=message_id,
                    attachment=attachment,
                )
                results.append(
                    attachment.model_copy(update={"local_path": local_path})
                )
                logger.info(
                    "attachment.downloaded",
                    filename=attachment.filename,
                    local_path=local_path,
                    bytes=attachment.size,
                )
            except Exception as exc:
                logger.warning(
                    "attachment.download_failed",
                    filename=attachment.filename,
                    message_id=message_id,
                    error=str(exc),
                )
                results.append(attachment)
        return results

    async def _download_one(
        self,
        inbox_id: str,
        message_id: str,
        attachment: EmailAttachment,
    ) -> str:
        """Download a single attachment and return its local path."""
        meta_url = (
            f"{self._base_url}/v0/inboxes/{inbox_id}/messages/{message_id}"
            f"/attachments/{attachment.attachment_id}"
        )
        meta_resp = await self._client.get(meta_url, timeout=15)
        meta_resp.raise_for_status()
        meta = meta_resp.json()
        download_url = meta.get("download_url")
        if not download_url:
            raise ValueError("AgentMail attachment metadata missing download_url")

        file_resp = await self._client.get(download_url, timeout=60)
        file_resp.raise_for_status()

        filename = self._safe_filename(attachment.filename)
        local_path = self._attachments_dir / filename
        suffix = 0
        while local_path.exists():
            suffix += 1
            stem = pathlib.Path(filename).stem
            ext = pathlib.Path(filename).suffix
            local_path = self._attachments_dir / f"{stem}_{suffix}{ext}"

        local_path.write_bytes(file_resp.content)
        return str(local_path)

    @staticmethod
    def _safe_filename(filename: str) -> str:
        """Remove path separators from the attachment filename."""
        safe = filename.replace("/", "_").replace("\\", "_").strip()
        return safe or "unnamed_attachment"


def _infer_region_from_email(email_data: InboundEmail, settings: Settings) -> str:
    """Infer the user's region from email data, sender, or settings.

    Checks in order:
    1. Email metadata region field (if available)
    2. Sender domain hints (e.g. .com.au -> Australia, .co.uk -> UK)
    3. DEFAULT_REGION setting
    Returns empty string if no region can be inferred.
    """
    # 1. Explicit metadata
    if hasattr(email_data, "metadata") and isinstance(email_data.metadata, dict):
        region = email_data.metadata.get("region") or email_data.metadata.get("country")
        if region:
            return str(region).strip()

    # 2. Detect from sender email domain or name
    sender = email_data.sender or ""
    detected = detect_region(sender)
    if detected:
        return detected

    # 3. Domain-based inference
    domain_region_map = {
        ".com.au": "Australia", ".au": "Australia",
        ".co.uk": "United Kingdom", ".uk": "United Kingdom",
        ".co.nz": "New Zealand", ".nz": "New Zealand",
        ".ca": "Canada",
        ".ie": "Ireland",
        ".sg": "Singapore",
        ".hk": "Hong Kong",
        ".jp": "Japan",
        ".cn": "China",
        ".de": "Germany",
        ".fr": "France",
        ".nl": "Netherlands",
    }
    sender_lower = sender.lower()
    for suffix, region in domain_region_map.items():
        if suffix in sender_lower:
            return region

    # 4. Default setting
    if getattr(settings, "default_region", None):
        return settings.default_region

    return ""


def _parse_inbound_email(payload: dict[str, Any]) -> InboundEmail:
    """Legacy fallback: normalize the old flat AgentMail payload shape.

    Real AgentMail events (v0.5.x) use a nested ``message`` / ``thread`` shape
    handled by ``parse_agentmail_event``. This helper remains for the
    smoke-test path and any old payloads that haven't been migrated.
    """
    from praxis.webhooks.payloads import _parse_attachments
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

    attachments = _parse_attachments(data.get("attachments"))

    return InboundEmail(
        message_id=data.get("id") or payload.get("id", ""),
        sender=sender,
        recipients=recipients,
        subject=data.get("subject", ""),
        body=data.get("body", ""),
        html_body=data.get("html_body"),
        attachments=attachments,
        received_at=datetime.now(UTC),
    )


def _failed_events_path(settings: Settings) -> pathlib.Path:
    return pathlib.Path(
        getattr(settings, "failed_events_path", None) or DEFAULT_FAILED_EVENTS_PATH
    )


def _persist_failed_event(
    settings: Settings,
    raw_body: bytes,
    exc: Exception,
    event_id: str,
    thread_id: str,
) -> None:
    """Write a failed webhook payload to the dead-letter log for later retry."""
    path = _failed_events_path(settings)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event_id": event_id,
        "thread_id": thread_id,
        "error": f"{type(exc).__name__}: {exc}",
        # Replay needs the original raw bytes plus headers; we store the payload.
        "payload": raw_body.decode("utf-8", errors="replace"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(orjson.dumps(entry).decode("utf-8") + "\n")
    except Exception as write_exc:
        logger.error(
            "webhook.failed_event_persist_error",
            event_id=event_id,
            error=str(write_exc),
        )


async def _download_attachments(
    inbound_email: InboundEmail,
    settings: Settings,
) -> InboundEmail:
    """If the email has attachments, download them and update local paths."""
    if not inbound_email.attachments:
        return inbound_email

    api_key = settings.agentmail_api_key or os.environ.get("AGENTMAIL_API_KEY", "")
    if not api_key:
        logger.warning(
            "attachment.no_api_key",
            message_id=inbound_email.message_id,
        )
        return inbound_email

    inbox_id = getattr(settings, "agentmail_inbox_id", None) or os.environ.get("AGENTMAIL_INBOX_ID", "")
    downloader = AttachmentDownloader(
        api_key=api_key,
        base_url="https://api.agentmail.to",
        attachments_dir=getattr(settings, "attachments_dir", None) or DEFAULT_ATTACHMENTS_DIR,
    )
    try:
        updated = await downloader.download_all(
            attachments=inbound_email.attachments,
            inbox_id=inbox_id,
            message_id=inbound_email.message_id,
        )
        inbound_email.attachments = updated
        return inbound_email
    finally:
        await downloader.close()


async def _invoke_graph(
    app: Any,
    email: InboundEmail,
    event_id: str,
    thread_id: str,
) -> None:
    """Run the LangGraph with the inbound email."""
    graph = getattr(app.state, "graph", None)
    if graph is None:
        msg = "Graph not initialized"
        raise RuntimeError(msg)

    logger.info(
        "webhook.graph_invoke",
        event_id=event_id,
        thread_id=thread_id,
        sender=email.sender,
        attachment_count=len(email.attachments),
    )

    from praxis.graph.state import AgentState

    settings = app.state.settings
    initial_state: AgentState = {
        "email_content": email,
        "metadata": AgentMetadata(thread_id=thread_id, workflow_id=event_id),
    }

    await graph.ainvoke(
        initial_state,
        config={
            "configurable": {
                "thread_id": thread_id,
                "settings": settings,
                "router": app.state.umans_router,
                "hermes_service": app.state.hermes_service,
                "ldr_service": getattr(app.state, "ldr_service", None),
                "document_service": getattr(app.state, "document_service", None),
                "agent_delegator": getattr(app.state, "agent_delegator", None),
                "user_region": _infer_region_from_email(email, settings) or "",
            },
        },
    )


async def _process_received_email(
    app: Any,
    email: InboundEmail,
    event_id: str,
    thread_id: str,
    settings: Settings,
    raw_body: bytes,
    *,
    is_retry: bool = False,
) -> WebhookResponse:
    """Download attachments and invoke the graph; handle failures gracefully.

    All errors are caught, persisted, and acknowledged so webhooks don't loop.
    """
    try:
        email = await _download_attachments(email, settings)
        await _invoke_graph(app, email, event_id, thread_id)
        status_label = "accepted_retry" if is_retry else "accepted"
        return WebhookResponse(
            status=status_label,
            event_id=event_id,
            thread_id=thread_id,
            message="Email processed successfully" if is_retry else "Email received and queued for processing",
        )
    except Exception as exc:
        logger.exception(
            "webhook.graph_failed",
            event_id=event_id,
            thread_id=thread_id,
            sender=email.sender,
            error=str(exc),
            is_retry=is_retry,
        )
        _persist_failed_event(settings, raw_body, exc, event_id, thread_id)
        return WebhookResponse(
            status="deferred",
            event_id=event_id,
            thread_id=thread_id,
            message="Processing deferred due to error; event logged for recovery.",
        )


@router.post("/email", status_code=status.HTTP_200_OK)
async def receive_email(request: Request) -> WebhookResponse:
    """Receive and verify an AgentMail webhook, then invoke the LangGraph.

    1. Read the raw body (needed for signature verification).
    2. Verify the Svix-compatible HMAC-SHA256 signature.
    3. Parse the payload with the new ``parse_agentmail_event``.
    4. Dispatch by event type:
       - ``message.received*`` → download attachments, build ``InboundEmail``,
         invoke the graph
       - ``message.delivered`` / ``message.sent`` → log + 200 OK
       - ``message.bounced`` / ``message.complained`` / ``message.rejected``
         → log warning + 200 OK
       - ``domain.verified`` → log + 200 OK
       - unknown event_type → 200 OK, logged as ignored

    Any exception after signature verification is caught, logged, persisted
    to the dead-letter log, and acknowledged with HTTP 200. This prevents
    undelivered webhook retries from storming the agent while still giving us
    full observability and the ability to replay manually.
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

    # ── Robust graph invocation for received (non-blocked) emails ──
    return await _process_received_email(
        app=request.app,
        email=email,
        event_id=event_id,
        thread_id=thread_id,
        settings=settings,
        raw_body=raw_body,
        is_retry=False,
    )
