"""Pydantic schemas for API requests, responses, and webhook payloads."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Response model for the ``GET /health`` endpoint."""

    status: str = "ok"
    version: str
    services: dict[str, str] = Field(
        default_factory=lambda: {
            "api": "ready",
            "neo4j": "pending",
            "qdrant": "pending",
        }
    )


class EmailData(BaseModel):
    """Nested email payload within an AgentMail webhook event."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from", description="Sender email address")
    to: list[str] = Field(description="Recipient email addresses")
    subject: str = Field(description="Email subject line")
    body: str = Field(description="Plain-text email body")
    html_body: str | None = Field(default=None, description="HTML body (if available)")
    attachments: list[dict] = Field(
        default_factory=list, description="Attachment metadata"
    )


class InboundEmailPayload(BaseModel):
    """AgentMail webhook payload for inbound email events.

    Svix-compatible structure with top-level ``id``, ``type``, ``timestamp``,
    and nested ``data`` containing the email content.
    """

    id: str = Field(description="Unique webhook event ID")
    type: str = Field(default="email.received", description="Event type")
    timestamp: datetime = Field(description="ISO 8601 event timestamp")
    data: EmailData


class WebhookResponse(BaseModel):
    """Response returned after accepting a webhook event."""

    status: str = "accepted"
    event_id: str
    message: str = "Email received and queued for processing"
