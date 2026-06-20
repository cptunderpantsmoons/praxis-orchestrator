"""Pydantic schemas for inbound emails, triage, memory, and agent output.

These schemas form the strict type contract between the FastAPI ingress,
the LangGraph state machine, Neo4j, Qdrant, and all tool outputs. Raw strings
are prohibited as tool returns; every exchange must validate against one of
these models.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# ── Inbound Email ─────────────────────────────────────────────────

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
    thread_id: str = ""
    message: str = "Email received and queued for processing"


# ── Triage ────────────────────────────────────────────────────────

class Priority(StrEnum):
    """Priority classification for inbound email."""

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class Intent(StrEnum):
    """Primary intent classification for inbound email."""

    GENERAL_INQUIRY = "general_inquiry"
    INVOICE_PROCESSING = "invoice_processing"
    SCHEDULE_MEETING = "schedule_meeting"
    RESEARCH_REQUEST = "research_request"
    CORRECTION = "correction"
    SPAM = "spam"


class Sentiment(StrEnum):
    """Sentiment classification for inbound email."""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class EmailTriage(BaseModel):
    """Structured output of the Triage Node (powered by Qwen)."""

    priority: Priority = Field(description="Priority level based on urgency and VIP status")
    intent: Intent = Field(description="Primary intent of the email")
    sentiment: Sentiment = Field(description="Sentiment analysis of the email")
    is_spam: bool = Field(description="True if the email is spam or phishing")
    sender_vip: bool = Field(description="True if the sender is marked as VIP")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Model confidence in the triage classification",
    )


# ── Memory Context ────────────────────────────────────────────────

class ThreadSummary(BaseModel):
    """Summary of a past thread retrieved from Neo4j."""

    thread_id: str
    subject: str
    last_message_summary: str
    timestamp: datetime


class CorrectionSummary(BaseModel):
    """Summary of a relevant past correction retrieved from Neo4j."""

    correction_id: str
    category: str
    rule: str
    confidence: float
    applied_count: int


class MemoryContext(BaseModel):
    """Context injected into the ReAct system prompt."""

    sender_history: list[ThreadSummary] = Field(default_factory=list)
    corrections: list[CorrectionSummary] = Field(default_factory=list)
    domain_facts: dict[str, Any] = Field(default_factory=dict)


# ── Agent Metadata ────────────────────────────────────────────────

class AgentMetadata(BaseModel):
    """Trace and timing metadata attached to every graph run."""

    thread_id: str = ""
    workflow_id: str = ""
    received_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    model_calls: dict[str, int] = Field(default_factory=lambda: {"qwen": 0, "kimi": 0, "glm": 0})


# ── Tool Outputs (Phase 2 stubs) ───────────────────────────────────

class ToolOutput(BaseModel):
    """Base class for all ReAct tool outputs."""

    tool_name: str
    success: bool


class SearchResult(ToolOutput):
    """Structured output of the dummy search tool."""

    query: str
    results: list[str]
    result_count: int

    def __init__(self, query: str, results: list[str], **kwargs: Any) -> None:
        super().__init__(
            tool_name="dummy_search",
            success=True,
            query=query,
            results=results,
            result_count=len(results),
            **kwargs,
        )


class CalculationResult(ToolOutput):
    """Structured output of the dummy calculator tool."""

    expression: str
    value: float
    unit: str = ""

    def __init__(self, expression: str, value: float, unit: str = "", **kwargs: Any) -> None:
        super().__init__(
            tool_name="dummy_calculator",
            success=True,
            expression=expression,
            value=value,
            unit=unit,
            **kwargs,
        )
