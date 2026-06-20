"""LangGraph AgentState definition.

The TypedDict serves as the single source of truth for the email-processing
graph. All nodes receive the same state shape and return partial updates.
"""

from __future__ import annotations

from typing import Annotated, NotRequired

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from praxis.models.schemas import (
    AgentMetadata,
    EmailTriage,
    InboundEmail,
    MemoryContext,
    ToolOutput,
)


class AgentState(TypedDict):
    """Shared, strongly typed state for the PRAXIS email-processing graph."""

    # Core email data from the webhook (REQ-206)
    email_content: InboundEmail

    # Triage classification (REQ-202 / REQ-203)
    triage_result: NotRequired[EmailTriage | None]

    # Memory context retrieved before reasoning (REQ-204)
    memory_context: NotRequired[MemoryContext]

    # Aggregated outputs from ReAct tools (REQ-205)
    tool_outputs: NotRequired[dict[str, ToolOutput]]

    # Final response or action plan produced by the ReAct loop
    final_response: NotRequired[str | None]

    # Trace and timing metadata
    metadata: NotRequired[AgentMetadata]

    # LangGraph conversation history; managed with add_messages reducer
    messages: NotRequired[Annotated[list[AnyMessage], add_messages]]
