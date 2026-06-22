"""Pydantic schemas for inbound emails, triage, memory, and agent output.

These schemas form the strict type contract between the FastAPI ingress,
the LangGraph state machine, Neo4j, Qdrant, and all tool outputs. Raw strings
are prohibited as tool returns; every exchange must validate against one of
these models.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from praxis.features.sender_style import SenderStyle

# ── Inbound Email ─────────────────────────────────────────────────

class EmailAttachment(BaseModel):
    """Metadata for an email attachment."""

    filename: str
    size: int = Field(ge=0, description="File size in bytes")
    content_type: str = "application/octet-stream"
    attachment_id: str = ""
    local_path: str = Field(
        default="",
        description="Filesystem path where the attachment was saved",
    )


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
    hindsight_memories: list[dict[str, Any]] = Field(default_factory=list)


# ── Agent Metadata ────────────────────────────────────────────────

class AgentMetadata(BaseModel):
    """Trace and timing metadata attached to every graph run."""

    thread_id: str = ""
    workflow_id: str = ""
    received_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    model_calls: dict[str, int] = Field(default_factory=lambda: {"qwen": 0, "kimi": 0, "glm": 0})
    # Tracks message IDs that have already been sent in this run, so the
    # dual-send fallback (reply → send) and ReAct tool calls can't double-send.
    sent_message_ids: set[str] = Field(default_factory=set)
    # Per-sender reply style loaded by context_loading_node (Task B4). ``None``
    # means no style is stored yet — the prompt builder will fall back to
    # default tone/signature. The runtime type is ``Any`` to avoid a circular
    # import: ``praxis.features.sender_style`` eagerly imports
    # ``praxis.services.neo4j_client``, which eagerly imports
    # ``CorrectionSummary``/``ThreadSummary`` from this module. If we annotated
    # this field as ``SenderStyle | None`` and imported ``SenderStyle`` at the
    # top of this file, Pydantic would try to resolve that forward reference
    # during class construction — but when ``sender_style`` is imported first
    # (a common case), the partial-load cycle leaves ``SenderStyle`` undefined
    # and ``AgentMetadata`` becomes uninstantiable. ``Any`` sidesteps the
    # resolution entirely; the ``TYPE_CHECKING`` import above keeps mypy/pyright
    # happy for callers that read ``metadata.sender_style.tone`` etc.
    sender_style: Any = None


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


class HermesRecallResult(ToolOutput):
    """Structured output of the hermes_recall tool.

    Combines Neo4j graph paths and Qdrant vector matches into a concise
    context summary synthesised by the Umans LLM (REQ-309).
    """

    query: str
    context_summary: str
    graph_paths: list[str] = Field(default_factory=list)
    vector_matches: list[str] = Field(default_factory=list)
    match_count: int

    def __init__(
        self,
        query: str,
        context_summary: str,
        graph_paths: list[str] | None = None,
        vector_matches: list[str] | None = None,
        success: bool = True,
        **kwargs: Any,
    ) -> None:
        gp = graph_paths or []
        vm = vector_matches or []
        super().__init__(
            tool_name="hermes_recall",
            success=success,
            query=query,
            context_summary=context_summary,
            graph_paths=gp,
            vector_matches=vm,
            match_count=len(gp) + len(vm),
            **kwargs,
        )


class HermesStoreResult(ToolOutput):
    """Structured output of the hermes_store tool.

    Confirms whether a fact was persisted to Neo4j and/or Qdrant (REQ-309).
    """

    fact: str
    stored_neo4j: bool
    stored_qdrant: bool
    fact_id: str = ""

    def __init__(
        self,
        fact: str,
        stored_neo4j: bool,
        stored_qdrant: bool,
        fact_id: str = "",
        success: bool = True,
        **kwargs: Any,
    ) -> None:
        ok = stored_neo4j or stored_qdrant
        super().__init__(
            tool_name="hermes_store",
            success=success and ok,
            fact=fact,
            stored_neo4j=stored_neo4j,
            stored_qdrant=stored_qdrant,
            fact_id=fact_id,
            **kwargs,
        )


class HermesLearnResult(ToolOutput):
    """Structured output of the hermes_learn tool.

    Parses unstructured correction text into structured facts via the Umans
    LLM and upserts corrections to Neo4j (REQ-309).
    """

    correction_text: str
    extracted_facts: list[str]
    corrections_applied: int

    def __init__(
        self,
        correction_text: str,
        extracted_facts: list[str] | None = None,
        corrections_applied: int = 0,
        success: bool = True,
        **kwargs: Any,
    ) -> None:
        ef = extracted_facts or []
        super().__init__(
            tool_name="hermes_learn",
            success=success,
            correction_text=correction_text,
            extracted_facts=ef,
            corrections_applied=corrections_applied,
            **kwargs,
        )


class LDRResult(ToolOutput):
    """Structured output of the deep_research tool (Local Deep Research).

    Contains the research summary, key findings, and source list.
    """

    query: str = ""
    summary: str = ""
    findings: list[Any] = []
    sources: list[Any] = []
    mode: str = "quick"

    def __init__(
        self,
        query: str = "",
        summary: str = "",
        findings: list | None = None,
        sources: list | None = None,
        mode: str = "quick",
        success: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            tool_name="deep_research",
            success=success,
            query=query,
            summary=summary,
            findings=findings or [],
            sources=sources or [],
            mode=mode,
            **kwargs,
        )


class DocumentAnalysisResult(ToolOutput):
    """Structured output of the analyze_document tool.

    Contains extracted text, structure, tables, metadata, and quality assessment.
    """

    file_path: str = ""
    file_type: str = ""
    page_count: int = 0
    word_count: int = 0
    text: str = ""
    quality_level: str = "unknown"
    quality_score: float = 0.0

    def __init__(
        self,
        file_path: str = "",
        file_type: str = "",
        page_count: int = 0,
        word_count: int = 0,
        text: str = "",
        quality_level: str = "unknown",
        quality_score: float = 0.0,
        success: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            tool_name="analyze_document",
            success=success,
            file_path=file_path,
            file_type=file_type,
            page_count=page_count,
            word_count=word_count,
            text=text,
            quality_level=quality_level,
            quality_score=quality_score,
            **kwargs,
        )


class DocumentCreationResult(ToolOutput):
    """Structured output of the create_report/create_memo/create_letter tools."""

    file_path: str = ""
    document_type: str = "report"
    title: str = ""

    def __init__(
        self,
        file_path: str = "",
        document_type: str = "report",
        title: str = "",
        success: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            tool_name="create_document",
            success=success,
            file_path=file_path,
            document_type=document_type,
            title=title,
            **kwargs,
        )


class AgentDelegationResult(ToolOutput):
    """Structured output of the delegate_to_agent tool."""

    agent_slug: str = ""
    agent_name: str = ""
    response: str = ""
    region: str = ""

    def __init__(
        self,
        agent_slug: str = "",
        agent_name: str = "",
        response: str = "",
        region: str = "",
        success: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            tool_name="delegate_to_agent",
            success=success,
            agent_slug=agent_slug,
            agent_name=agent_name,
            response=response,
            region=region,
            **kwargs,
        )


class AgentListingResult(ToolOutput):
    """Structured output of the list_agents tool."""

    division: str = ""
    keyword: str = ""
    region: str = ""
    agents: list[Any] = []

    def __init__(
        self,
        division: str = "",
        keyword: str = "",
        region: str = "",
        agents: list | None = None,
        success: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            tool_name="list_agents",
            success=success,
            division=division,
            keyword=keyword,
            region=region,
            agents=agents or [],
            **kwargs,
        )


class EmailToolResult(ToolOutput):
    """Structured output of email send/reply tools.

    Wraps the string result from AgentMail into a Pydantic model so it
    integrates with the ReAct loop's ``model_dump_json()`` contract.
    """

    message: str
    message_id: str = ""
    body: str = ""

    def __init__(self, message: str, message_id: str = "", **kwargs: Any) -> None:
        super().__init__(
            tool_name=kwargs.pop("tool_name", "email"),
            success=kwargs.pop("success", True),
            message=message,
            message_id=message_id,
            **kwargs,
        )
