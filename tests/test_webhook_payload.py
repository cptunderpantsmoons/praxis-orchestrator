"""Tests for the AgentMail webhook payload parser.

The real AgentMail webhook payload shape (from
https://docs.agentmail.to/events.md) is::

    {
        "type": "event",
        "event_type": "message.received",
        "event_id": "evt_123abc...",
        "message": { ... Message fields ... },
        "thread":  { ... Thread fields ... }
    }

Different event types carry different sub-objects:
- ``message.received*``        → ``message`` + ``thread``
- ``message.sent``             → ``send``
- ``message.delivered``        → ``delivery``
- ``message.bounced``          → ``bounce``
- ``message.complained``       → ``complaint``
- ``message.rejected``         → ``reject``
- ``domain.verified``          → ``domain``

The parser must:
- Return a typed ``ParsedEvent`` for actionable events
- Return ``None`` for unknown / non-actionable events
- Convert a ``message.received*`` event into an ``InboundEmail``
"""

from __future__ import annotations

from datetime import UTC, datetime

from praxis.webhooks.payloads import (
    parse_agentmail_event,
)

# ── Happy paths ────────────────────────────────────────────────


def test_parse_message_received() -> None:
    payload = {
        "type": "event",
        "event_type": "message.received",
        "event_id": "evt_abc123",
        "message": {
            "id": "msg_001",
            "from_": "alice@example.com",  # 'from' is reserved in Python
            "to": ["agent@praxis.to"],
            "subject": "Invoice question",
            "body": "Please find attached...",
            "html_body": "<p>Please find attached...</p>",
            "created_at": "2026-06-20T08:00:00Z",
        },
        "thread": {
            "thread_id": "thr_001",
            "message_count": 1,
        },
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    assert evt.event_id == "evt_abc123"
    assert evt.event_type == "message.received"
    assert evt.thread_id == "thr_001"
    assert evt.message_id == "msg_001"
    assert evt.sender == "alice@example.com"
    assert evt.recipients == ["agent@praxis.to"]
    assert evt.subject == "Invoice question"
    assert evt.body == "Please find attached..."
    assert evt.html_body == "<p>Please find attached...</p>"


def test_parse_message_received_spam() -> None:
    payload = {
        "event_type": "message.received.spam",
        "event_id": "evt_spam",
        "message": {"id": "m1", "from_": "spammer@bad.com", "to": ["a@b.com"]},
        "thread": {"thread_id": "t1"},
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    assert evt.is_spam is True
    assert evt.event_type == "message.received.spam"


def test_parse_message_received_blocked() -> None:
    payload = {
        "event_type": "message.received.blocked",
        "event_id": "evt_block",
        "message": {"id": "m1", "from_": "x@y.com", "to": ["a@b.com"]},
        "thread": {"thread_id": "t1"},
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    assert evt.is_blocked is True


def test_parse_message_received_unauthenticated() -> None:
    payload = {
        "event_type": "message.received.unauthenticated",
        "event_id": "evt_unauth",
        "message": {"id": "m1", "from_": "x@y.com", "to": ["a@b.com"]},
        "thread": {"thread_id": "t1"},
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    assert evt.is_unauthenticated is True


def test_parse_message_received_with_attachments() -> None:
    """The parser extracts attachment metadata including attachment_id."""
    payload = {
        "event_type": "message.received",
        "event_id": "evt_attach",
        "message": {
            "id": "msg_002",
            "from_": "bob@example.com",
            "to": ["agent@praxis.to"],
            "subject": "Spreadsheet analysis request",
            "body": "Please analyse",
            "attachments": [
                {
                    "filename": "data.xlsx",
                    "size": 1024,
                    "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "attachment_id": "att_123",
                }
            ],
        },
        "thread": {"thread_id": "thr_002"},
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    assert len(evt.attachments) == 1
    att = evt.attachments[0]
    assert att.filename == "data.xlsx"
    assert att.size == 1024
    assert att.attachment_id == "att_123"
    assert att.content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    inbound = evt.to_inbound_email()
    assert inbound is not None
    assert len(inbound.attachments) == 1
    assert inbound.attachments[0].filename == "data.xlsx"


# ── Non-received events ────────────────────────────────────────


def test_parse_message_sent() -> None:
    payload = {
        "event_type": "message.sent",
        "event_id": "evt_sent",
        "send": {
            "inbox_id": "ib_1",
            "message_id": "m1",
            "recipients": ["alice@example.com"],
        },
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    assert evt.event_type == "message.sent"
    assert evt.recipients == ["alice@example.com"]


def test_parse_message_delivered() -> None:
    payload = {
        "event_type": "message.delivered",
        "event_id": "evt_delivered",
        "delivery": {
            "message_id": "m1",
            "recipients": ["alice@example.com"],
            "delivered_at": "2026-06-20T08:01:00Z",
        },
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    assert evt.event_type == "message.delivered"
    assert evt.recipients == ["alice@example.com"]


def test_parse_message_bounced() -> None:
    payload = {
        "event_type": "message.bounced",
        "event_id": "evt_bounced",
        "bounce": {
            "message_id": "m1",
            "type": "hard",
            "diagnostic": "550 user unknown",
        },
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    assert evt.event_type == "message.bounced"
    assert evt.bounce_type == "hard"
    assert evt.bounce_diagnostic == "550 user unknown"


def test_parse_message_complained() -> None:
    payload = {
        "event_type": "message.complained",
        "event_id": "evt_complaint",
        "complaint": {"message_id": "m1", "type": "spam"},
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    assert evt.complaint_type == "spam"


def test_parse_message_rejected() -> None:
    payload = {
        "event_type": "message.rejected",
        "event_id": "evt_rejected",
        "reject": {"message_id": "m1", "reason": "policy violation"},
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    assert evt.reject_reason == "policy violation"


def test_parse_domain_verified() -> None:
    payload = {
        "event_type": "domain.verified",
        "event_id": "evt_domain",
        "domain": {"domain": "example.com", "status": "verified"},
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    assert evt.event_type == "domain.verified"
    assert evt.domain_name == "example.com"


# ── Edge cases ─────────────────────────────────────────────────


def test_unknown_event_type_returns_none() -> None:
    payload = {"event_type": "weird.future.event", "event_id": "evt_x"}
    evt = parse_agentmail_event(payload)
    assert evt is None


def test_missing_event_type_returns_none() -> None:
    payload = {"event_id": "evt_x", "data": "something"}
    evt = parse_agentmail_event(payload)
    assert evt is None


def test_empty_payload_returns_none() -> None:
    assert parse_agentmail_event({}) is None
    assert parse_agentmail_event(None) is None  # type: ignore[arg-type]


def test_message_received_with_missing_optional_fields() -> None:
    """If 'message' only has id, parser should still succeed (with empty defaults)."""
    payload = {
        "event_type": "message.received",
        "event_id": "evt_min",
        "message": {"id": "m1"},
        "thread": {"thread_id": "t1"},
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    assert evt.message_id == "m1"
    assert evt.sender == ""
    assert evt.recipients == []
    assert evt.subject == ""


def test_parsed_event_to_inbound_email() -> None:
    """A parsed message.received event can be converted to InboundEmail."""
    payload = {
        "event_type": "message.received",
        "event_id": "evt_001",
        "message": {
            "id": "msg_001",
            "from_": "alice@example.com",
            "to": ["agent@praxis.to"],
            "subject": "Hello",
            "body": "World",
        },
        "thread": {"thread_id": "thr_001"},
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    inbound = evt.to_inbound_email()
    assert inbound.message_id == "msg_001"
    assert inbound.sender == "alice@example.com"
    assert inbound.recipients == ["agent@praxis.to"]
    assert inbound.subject == "Hello"
    assert inbound.body == "World"
    # received_at should be a recent datetime
    assert (datetime.now(UTC) - inbound.received_at).total_seconds() < 60


def test_parsed_event_to_inbound_email_returns_none_for_non_received() -> None:
    """to_inbound_email() only works for message.received* events."""
    payload = {
        "event_type": "message.sent",
        "event_id": "evt_sent",
        "send": {"recipients": ["x@y.com"]},
    }
    evt = parse_agentmail_event(payload)
    assert evt is not None
    assert evt.to_inbound_email() is None
