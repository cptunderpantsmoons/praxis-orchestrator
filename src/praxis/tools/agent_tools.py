"""StructuredTool wrappers for the Agency ReAct agent's non-email tools.

Critical #2 from the v0.2.0 whole-branch review: ``_react_native`` only
bound 5 of the 13 spec-required tools. The other 8 (hermes_recall,
hermes_store, hermes_learn, deep_research, analyze_document,
create_report, delegate_to_agent, list_agents) were reachable via
``_execute_tool`` name-dispatch in legacy mode but not exposed as
``StructuredTool`` objects for ``bind_tools`` — so the native model had
no way to discover or invoke them.

This module provides factory functions that build ``StructuredTool``
wrappers with correct Pydantic arg schemas. The wrappers delegate to
``_execute_tool`` (the single source of truth for tool dispatch) so
both the native and legacy paths share the same execution logic. The
services (hermes, ldr, document, agent_delegator) are bound via closure
when the factory is called in ``_react_native``.

The ``update_sender_style`` tool is imported from
``praxis.features.sender_style`` and bound conditionally in
``_react_native`` (not here) so the feature-flag check stays local to
the react node.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from praxis.graph.state import AgentState


# ── Pydantic arg schemas ────────────────────────────────────────────
# These define the JSON schema the Umans API advertises to the model.
# They MUST match the kwargs ``_execute_tool`` reads from the tool_call
# dict, otherwise the model's arguments won't reach the service.


class HermesRecallArgs(BaseModel):
    """Args for the ``hermes_recall`` tool."""

    query: str = Field(description="What to look up in long-term memory")


class HermesStoreArgs(BaseModel):
    """Args for the ``hermes_store`` tool."""

    fact: str = Field(description="The fact to remember")


class HermesLearnArgs(BaseModel):
    """Args for the ``hermes_learn`` tool."""

    correction: str = Field(description="The correction text to learn from")


class DeepResearchArgs(BaseModel):
    """Args for the ``deep_research`` tool."""

    query: str = Field(description="The research question")
    mode: str = Field(
        default="quick",
        description="Research mode: 'quick' (fast summary) or 'full' (detailed report)",
    )


class AnalyzeDocumentArgs(BaseModel):
    """Args for the ``analyze_document`` tool."""

    file_path: str = Field(description="Path to the document (.docx, .pdf, .xlsx, .csv, .pptx, .txt)")


class CreateReportArgs(BaseModel):
    """Args for the ``create_report`` tool."""

    title: str = Field(description="Report title")
    sections: str = Field(
        description=(
            "Report sections as 'Heading1::content1||Heading2::content2'. "
            "Each section is a heading and content separated by '::', sections "
            "are separated by '||'."
        ),
    )


class DelegateToAgentArgs(BaseModel):
    """Args for the ``delegate_to_agent`` tool."""

    agent_slug: str = Field(description="Agent identifier (e.g. 'finance-financial-analyst')")
    task: str = Field(description="The task/question to delegate")
    context: str = Field(default="", description="Optional additional context")
    region: str = Field(default="", description="User region for region-aware advice")


class ListAgentsArgs(BaseModel):
    """Args for the ``list_agents`` tool."""

    division: str = Field(default="", description="Optional division filter")
    keyword: str = Field(default="", description="Optional search keyword")
    region: str = Field(default="", description="Optional region filter")


# ── Factory ─────────────────────────────────────────────────────────


def build_agent_tools(
    *,
    hermes_service: Any | None = None,
    ldr_service: Any | None = None,
    document_service: Any | None = None,
    agent_delegator: Any | None = None,
    sent_message_ids: set[str] | None = None,
    state: "AgentState | None" = None,
) -> list[StructuredTool]:
    """Build the 8 non-email ``StructuredTool`` wrappers for native mode.

    The returned tools delegate to ``_execute_tool`` (imported lazily to
    avoid a circular import: ``praxis.graph.nodes`` imports
    ``praxis.tools.email_tools`` at module load, and ``email_tools`` does
    NOT import this module, but keeping the import local to the factory
    makes the dependency direction explicit and testable).

    The services are bound via closure so the tool functions can call
    ``_execute_tool`` with the right service instances without the model
    having to pass them.

    Args:
        hermes_service: HermesService instance (or None for graceful degradation).
        ldr_service: LdrService instance (or None).
        document_service: DocumentService instance (or None).
        agent_delegator: AgentDelegator instance (or None).
        sent_message_ids: Shared dedup set for reply_email/send_email (passed
            through to ``_execute_tool`` so the dedup guard works in native
            mode too — though email tools are bound separately in
            ``_react_native``, this keeps the guard consistent if the model
            calls them via this path).
        state: The current ``AgentState``, used by ``_execute_tool`` to
            extract the inbound ``message_id`` for dedup tracking.

    Returns:
        List of 8 ``StructuredTool`` instances: hermes_recall,
        hermes_store, hermes_learn, deep_research, analyze_document,
        create_report, delegate_to_agent, list_agents.
    """
    # Lazy import to avoid circular dependency at module load time.
    from praxis.graph.nodes import _execute_tool

    async def _hermes_recall(query: str) -> Any:
        _name, result = await _execute_tool(
            {"name": "hermes_recall", "query": query},
            hermes_service=hermes_service,
            ldr_service=ldr_service,
            document_service=document_service,
            agent_delegator=agent_delegator,
            sent_message_ids=sent_message_ids,
            state=state,
        )
        return result

    async def _hermes_store(fact: str) -> Any:
        _name, result = await _execute_tool(
            {"name": "hermes_store", "fact": fact},
            hermes_service=hermes_service,
            ldr_service=ldr_service,
            document_service=document_service,
            agent_delegator=agent_delegator,
            sent_message_ids=sent_message_ids,
            state=state,
        )
        return result

    async def _hermes_learn(correction: str) -> Any:
        _name, result = await _execute_tool(
            {"name": "hermes_learn", "correction": correction},
            hermes_service=hermes_service,
            ldr_service=ldr_service,
            document_service=document_service,
            agent_delegator=agent_delegator,
            sent_message_ids=sent_message_ids,
            state=state,
        )
        return result

    async def _deep_research(query: str, mode: str = "quick") -> Any:
        _name, result = await _execute_tool(
            {"name": "deep_research", "query": query, "mode": mode},
            hermes_service=hermes_service,
            ldr_service=ldr_service,
            document_service=document_service,
            agent_delegator=agent_delegator,
            sent_message_ids=sent_message_ids,
            state=state,
        )
        return result

    async def _analyze_document(file_path: str) -> Any:
        _name, result = await _execute_tool(
            {"name": "analyze_document", "file_path": file_path},
            hermes_service=hermes_service,
            ldr_service=ldr_service,
            document_service=document_service,
            agent_delegator=agent_delegator,
            sent_message_ids=sent_message_ids,
            state=state,
        )
        return result

    async def _create_report(title: str, sections: str) -> Any:
        _name, result = await _execute_tool(
            {"name": "create_report", "title": title, "sections": sections},
            hermes_service=hermes_service,
            ldr_service=ldr_service,
            document_service=document_service,
            agent_delegator=agent_delegator,
            sent_message_ids=sent_message_ids,
            state=state,
        )
        return result

    async def _delegate_to_agent(
        agent_slug: str,
        task: str,
        context: str = "",
        region: str = "",
    ) -> Any:
        _name, result = await _execute_tool(
            {
                "name": "delegate_to_agent",
                "agent_slug": agent_slug,
                "task": task,
                "context": context,
                "region": region,
            },
            hermes_service=hermes_service,
            ldr_service=ldr_service,
            document_service=document_service,
            agent_delegator=agent_delegator,
            sent_message_ids=sent_message_ids,
            state=state,
        )
        return result

    async def _list_agents(
        division: str = "",
        keyword: str = "",
        region: str = "",
    ) -> Any:
        _name, result = await _execute_tool(
            {
                "name": "list_agents",
                "division": division,
                "keyword": keyword,
                "region": region,
            },
            hermes_service=hermes_service,
            ldr_service=ldr_service,
            document_service=document_service,
            agent_delegator=agent_delegator,
            sent_message_ids=sent_message_ids,
            state=state,
        )
        return result

    hermes_recall_tool = StructuredTool.from_function(
        coroutine=_hermes_recall,
        name="hermes_recall",
        description=(
            "Recall relevant context from long-term memory (Neo4j graph + "
            "Qdrant vectors). Use when you need past interactions, sender "
            "history, or stored facts. Args: query."
        ),
        args_schema=HermesRecallArgs,
    )

    hermes_store_tool = StructuredTool.from_function(
        coroutine=_hermes_store,
        name="hermes_store",
        description=(
            "Store a fact in long-term memory for future recall. "
            "Args: fact."
        ),
        args_schema=HermesStoreArgs,
    )

    hermes_learn_tool = StructuredTool.from_function(
        coroutine=_hermes_learn,
        name="hermes_learn",
        description=(
            "Learn from a user correction. Extracts structured facts and "
            "upserts corrections to the graph. Args: correction."
        ),
        args_schema=HermesLearnArgs,
    )

    deep_research_tool = StructuredTool.from_function(
        coroutine=_deep_research,
        name="deep_research",
        description=(
            "Local Deep Research (LDR). Compile verified, cited reports "
            "for market intelligence, factual verification, or competitive "
            "analysis. Args: query, mode (quick|full)."
        ),
        args_schema=DeepResearchArgs,
    )

    analyze_document_tool = StructuredTool.from_function(
        coroutine=_analyze_document,
        name="analyze_document",
        description=(
            "Extract text, structure, and metadata from an attached document "
            "(.docx, .pdf, .xlsx, .csv, .pptx, .txt). Args: file_path."
        ),
        args_schema=AnalyzeDocumentArgs,
    )

    create_report_tool = StructuredTool.from_function(
        coroutine=_create_report,
        name="create_report",
        description=(
            "Generate a formatted .docx report deliverable. Args: title, "
            "sections (format: 'Heading1::content1||Heading2::content2')."
        ),
        args_schema=CreateReportArgs,
    )

    delegate_to_agent_tool = StructuredTool.from_function(
        coroutine=_delegate_to_agent,
        name="delegate_to_agent",
        description=(
            "Delegate a task to a specialized agent from The Agency Roster. "
            "Args: agent_slug, task, context (optional), region (optional)."
        ),
        args_schema=DelegateToAgentArgs,
    )

    list_agents_tool = StructuredTool.from_function(
        coroutine=_list_agents,
        name="list_agents",
        description=(
            "List available agents from The Agency Roster, optionally "
            "filtered by division, keyword, or region. Args: division, "
            "keyword, region (all optional)."
        ),
        args_schema=ListAgentsArgs,
    )

    return [
        hermes_recall_tool,
        hermes_store_tool,
        hermes_learn_tool,
        deep_research_tool,
        analyze_document_tool,
        create_report_tool,
        delegate_to_agent_tool,
        list_agents_tool,
    ]


__all__ = [
    "AnalyzeDocumentArgs",
    "CreateReportArgs",
    "DeepResearchArgs",
    "DelegateToAgentArgs",
    "HermesLearnArgs",
    "HermesRecallArgs",
    "HermesStoreArgs",
    "ListAgentsArgs",
    "build_agent_tools",
]
