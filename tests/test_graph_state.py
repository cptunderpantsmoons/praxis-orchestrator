"""Tests for the AgentState schema and LangGraph state serialization."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from praxis.graph.state import AgentState
from praxis.models.schemas import (
    AgentMetadata,
    EmailTriage,
    InboundEmail,
    Intent,
    MemoryContext,
    Priority,
    Sentiment,
)


def test_agent_state_can_be_constructed() -> None:
    """AgentState is a valid TypedDict with the expected keys."""
    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="user@example.com",
            subject="Hello",
            body="Test",
        ),
    }
    assert state["email_content"].sender == "user@example.com"
    assert state.get("triage_result") is None


def test_agent_state_accepts_full_fields() -> None:
    """AgentState accepts all fields defined in the schema."""
    triage = EmailTriage(
        priority=Priority.HIGH,
        intent=Intent.INVOICE_PROCESSING,
        sentiment=Sentiment.NEUTRAL,
        is_spam=False,
        sender_vip=True,
        confidence=0.95,
    )
    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="user@example.com",
            subject="Invoice",
            body="Please pay invoice #123",
        ),
        "triage_result": triage,
        "memory_context": MemoryContext(),
        "tool_outputs": {},
        "final_response": "Done",
        "metadata": AgentMetadata(thread_id="t1"),
        "messages": [HumanMessage(content="Hello"), AIMessage(content="Hi")],
    }
    assert state["triage_result"].priority == Priority.HIGH
    assert state["final_response"] == "Done"
