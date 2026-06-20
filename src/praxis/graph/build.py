"""LangGraph builder and conditional edge routing.

Builds the email-processing graph with typed state and wires:
  triage -> discard | context_loading -> react
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from praxis.models.schemas import Intent, Priority

from .checkpointer import InMemoryFallback
from .nodes import (
    context_loading_node,
    correction_node,
    discard_node,
    react_node,
    triage_node,
)
from .state import AgentState


def _route_triage(state: AgentState) -> str:
    """Return the next node based on the triage classification.

    Priority routing:
    - HIGH priority emails skip context loading (fast-path) and go directly to react.
    - Spam goes to discard.
    - Correction intent goes to correction_node.
    - Everything else goes through context_loading for similarity search.
    """
    triage = state.get("triage_result")
    if triage is None:
        return "context_loading"
    if triage.is_spam:
        return "discard"
    if triage.intent == Intent.CORRECTION:
        return "correction_node"
    if getattr(triage, "priority", None) == Priority.HIGH:
        return "react"  # Fast-path: skip context loading for HIGH priority
    return "context_loading"


def build_graph(checkpointer: Any | None = None) -> StateGraph:
    """Compile and return the PRAXIS email-processing graph.

    Args:
        checkpointer: Optional LangGraph checkpointer. If omitted, an
            in-memory checkpointer is used so tests can run without Postgres.

    Returns:
        A compiled StateGraph ready for invocation.
    """
    if checkpointer is None:
        checkpointer = InMemoryFallback()

    builder = StateGraph(state_schema=AgentState)

    builder.add_node("triage", triage_node)
    builder.add_node("context_loading", context_loading_node)
    builder.add_node("react", react_node)
    builder.add_node("discard", discard_node)
    builder.add_node("correction_node", correction_node)

    builder.add_edge(START, "triage")
    builder.add_conditional_edges(
        "triage",
        _route_triage,
        {
            "discard": "discard",
            "correction_node": "correction_node",
            "react": "react",  # Fast-path for HIGH priority
            "context_loading": "context_loading",
        },
    )
    builder.add_edge("context_loading", "react")
    builder.add_edge("react", END)
    builder.add_edge("discard", END)
    builder.add_edge("correction_node", END)

    return builder.compile(checkpointer=checkpointer)


__all__ = ["build_graph"]
