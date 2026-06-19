"""Pydantic schemas for inbound emails and webhook payloads."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EmailAttachment(BaseModel):
    """Metadata for an email attachment."""

    filename: str
    size: int = Field(ge=0, description="File size in bytes")
    content_type: str = "application/octet-stream"


class InboundEmail(BaseModel):
    """Canonical representation of an inbound email.

    This is the unified schema used across the pipeline regardless of
    whether the email arrived via AgentMail webhook or IMAP (Phase 5).
    """

    message_id: str = Field(description="Unique message identifier")
    sender: str = Field(description="Sender email address")
    recipients: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = Field(default="", description="Plain-text body")
    html_body: str | None = Field(default=None, description="HTML body if available")
    attachments: list[EmailAttachment] = Field(default_factory=list)
    received_at: datetime = Field(default_factory=datetime.now)


class WebhookResponse(BaseModel):
    """Standard response for the webhook endpoint."""

    status: str = "accepted"
    event_id: str = ""
    message: str = "Email received and queued for processing"
