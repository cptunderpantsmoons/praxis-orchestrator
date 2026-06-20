"""AgentMail webhook payload parser.

The real AgentMail webhook payload shape (from
https://docs.agentmail.to/events.md) is::

    {
        "type": "event",
        "event_type": "message.received",
        "event_id": "evt_123abc...",
        "message": { ... },  # for message.received*
        "thread":  { ... },  # for message.received*
        # OR for other event types:
        "send":      { ... },  # message.sent
        "delivery":  { ... },  # message.delivered
        "bounce":    { ... },  # message.bounced
        "complaint": { ... },  # message.complained
        "reject":    { ... },  # message.rejected
        "domain":    { ... },  # domain.verified
    }

Reference: https://docs.agentmail.to/events.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from praxis.models.schemas import InboundEmail

# ── Event-type constants ──────────────────────────────────────

EVENT_MESSAGE_RECEIVED = "message.received"
EVENT_MESSAGE_RECEIVED_SPAM = "message.received.spam"
EVENT_MESSAGE_RECEIVED_BLOCKED = "message.received.blocked"
EVENT_MESSAGE_RECEIVED_UNAUTH = "message.received.unauthenticated"
EVENT_MESSAGE_SENT = "message.sent"
EVENT_MESSAGE_DELIVERED = "message.delivered"
EVENT_MESSAGE_BOUNCED = "message.bounced"
EVENT_MESSAGE_COMPLAINED = "message.complained"
EVENT_MESSAGE_REJECTED = "message.rejected"
EVENT_DOMAIN_VERIFIED = "domain.verified"

RECEIVED_EVENTS = {
    EVENT_MESSAGE_RECEIVED,
    EVENT_MESSAGE_RECEIVED_SPAM,
    EVENT_MESSAGE_RECEIVED_BLOCKED,
    EVENT_MESSAGE_RECEIVED_UNAUTH,
}

KNOWN_EVENTS = RECEIVED_EVENTS | {
    EVENT_MESSAGE_SENT,
    EVENT_MESSAGE_DELIVERED,
    EVENT_MESSAGE_BOUNCED,
    EVENT_MESSAGE_COMPLAINED,
    EVENT_MESSAGE_REJECTED,
    EVENT_DOMAIN_VERIFIED,
}


# ── Parsed result ─────────────────────────────────────────────


@dataclass
class ParsedEvent:
    """Typed view of an AgentMail webhook event.

    Only the fields relevant to the event_type are populated; the rest are None.
    """

    event_id: str
    event_type: str

    # Received-event fields (message.received*)
    message_id: str | None = None
    thread_id: str | None = None
    sender: str = ""
    recipients: list[str] = field(default_factory=list)
    subject: str = ""
    body: str = ""
    html_body: str | None = None
    is_spam: bool = False
    is_blocked: bool = False
    is_unauthenticated: bool = False

    # Sent / delivered / bounced / complained / rejected fields
    bounce_type: str | None = None
    bounce_diagnostic: str | None = None
    complaint_type: str | None = None
    reject_reason: str | None = None

    # Domain events
    domain_name: str | None = None

    def is_received(self) -> bool:
        """True for any ``message.received*`` event (including spam/blocked)."""
        return self.event_type in RECEIVED_EVENTS

    def to_inbound_email(self) -> InboundEmail | None:
        """Convert a ``message.received*`` event to ``InboundEmail``.

        Returns ``None`` for non-received events. The ``received_at`` timestamp
        is set to the current UTC time because AgentMail events don't carry
        the original receive time in the same field; downstream nodes can
        record the difference if needed.
        """
        if not self.is_received():
            return None
        return InboundEmail(
            message_id=self.message_id or "",
            sender=self.sender,
            recipients=list(self.recipients),
            subject=self.subject,
            body=self.body,
            html_body=self.html_body,
            received_at=datetime.now(UTC),
        )


# ── Parser ────────────────────────────────────────────────────


def _extract_address(value: Any) -> str:
    """Accept either a bare string or ``{"email": "...", "name": "..."}``."""
    if isinstance(value, dict):
        return str(value.get("email", ""))
    return str(value) if value is not None else ""


def _extract_recipients(value: Any) -> list[str]:
    """Normalize the ``to`` field — list of strings, list of dicts, or single value."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [_extract_address(value)]
    if isinstance(value, list):
        return [_extract_address(item) for item in value]
    return [str(value)]


def _parse_received(message: dict[str, Any], thread: dict[str, Any], event_type: str) -> ParsedEvent:
    return ParsedEvent(
        event_id="",  # set by caller
        event_type=event_type,
        message_id=str(message.get("id", "") or ""),
        thread_id=str(thread.get("thread_id", "") or "") or None,
        sender=_extract_address(message.get("from_") or message.get("from") or message.get("sender")),
        recipients=_extract_recipients(message.get("to")),
        subject=str(message.get("subject", "") or ""),
        body=str(message.get("body", "") or ""),
        html_body=message.get("html_body") or message.get("html"),
        is_spam=(event_type == EVENT_MESSAGE_RECEIVED_SPAM),
        is_blocked=(event_type == EVENT_MESSAGE_RECEIVED_BLOCKED),
        is_unauthenticated=(event_type == EVENT_MESSAGE_RECEIVED_UNAUTH),
    )


def parse_agentmail_event(payload: dict[str, Any] | None) -> ParsedEvent | None:
    """Parse an AgentMail webhook payload into a ``ParsedEvent``.

    Returns ``None`` for unknown event types, empty payloads, or ``None``
    input — callers should treat that as "acknowledge and ignore".
    """
    if not payload or not isinstance(payload, dict):
        return None

    event_type = payload.get("event_type")
    if not event_type or event_type not in KNOWN_EVENTS:
        return None

    event_id = str(payload.get("event_id", "") or "")

    if event_type in RECEIVED_EVENTS:
        message = payload.get("message") or {}
        thread = payload.get("thread") or {}
        evt = _parse_received(message, thread, event_type)
        evt.event_id = event_id
        return evt

    if event_type == EVENT_MESSAGE_SENT:
        send = payload.get("send") or {}
        return ParsedEvent(
            event_id=event_id,
            event_type=event_type,
            message_id=str(send.get("message_id", "") or "") or None,
            recipients=_extract_recipients(send.get("recipients")),
        )

    if event_type == EVENT_MESSAGE_DELIVERED:
        delivery = payload.get("delivery") or {}
        return ParsedEvent(
            event_id=event_id,
            event_type=event_type,
            message_id=str(delivery.get("message_id", "") or "") or None,
            recipients=_extract_recipients(delivery.get("recipients")),
        )

    if event_type == EVENT_MESSAGE_BOUNCED:
        bounce = payload.get("bounce") or {}
        return ParsedEvent(
            event_id=event_id,
            event_type=event_type,
            message_id=str(bounce.get("message_id", "") or "") or None,
            bounce_type=bounce.get("type"),
            bounce_diagnostic=bounce.get("diagnostic"),
        )

    if event_type == EVENT_MESSAGE_COMPLAINED:
        complaint = payload.get("complaint") or {}
        return ParsedEvent(
            event_id=event_id,
            event_type=event_type,
            message_id=str(complaint.get("message_id", "") or "") or None,
            complaint_type=complaint.get("type"),
        )

    if event_type == EVENT_MESSAGE_REJECTED:
        reject = payload.get("reject") or {}
        return ParsedEvent(
            event_id=event_id,
            event_type=event_type,
            message_id=str(reject.get("message_id", "") or "") or None,
            reject_reason=reject.get("reason"),
        )

    if event_type == EVENT_DOMAIN_VERIFIED:
        domain = payload.get("domain") or {}
        return ParsedEvent(
            event_id=event_id,
            event_type=event_type,
            domain_name=domain.get("domain"),
        )

    return None  # Should be unreachable
