"""Tests for the Triage Node structured output."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from praxis.graph.nodes import triage_node
from praxis.graph.state import AgentState
from praxis.models.schemas import (
    EmailTriage,
    InboundEmail,
    Intent,
    Priority,
    Sentiment,
)


def _make_mock_model(content: str) -> MagicMock:
    """Create a mock model that returns the given content."""
    mock = MagicMock()
    # ainvoke() returns the result directly (LangChain 1.4.8 behavior)
    mock.ainvoke = MagicMock(return_value=MagicMock(content=content))
    return mock


@pytest.mark.asyncio
async def test_triage_node_returns_structured_output() -> None:
    """The triage node returns a validated EmailTriage model."""
    expected = EmailTriage(
        priority=Priority.HIGH,
        intent=Intent.INVOICE_PROCESSING,
        sentiment=Sentiment.NEUTRAL,
        is_spam=False,
        sender_vip=True,
        confidence=0.95,
    )
    mock_model = _make_mock_model(expected.model_dump_json())

    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="vip@example.com",
            subject="Urgent invoice",
            body="Please pay immediately.",
        ),
    }

    with patch("praxis.graph.nodes.UmansChatModel.create", return_value=mock_model):
        result = await triage_node(
            state,
            config={"configurable": {}},
        )

    triage = result["triage_result"]
    assert isinstance(triage, EmailTriage)
    assert triage.priority == Priority.HIGH
    assert triage.intent == Intent.INVOICE_PROCESSING
    assert triage.sender_vip is True


@pytest.mark.asyncio
async def test_triage_node_falls_back_on_invalid_model_response() -> None:
    """When the model emits non-JSON, the node falls back to neutral triage."""
    mock_model = _make_mock_model("not valid json")

    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="user@example.com",
            subject="Hello",
            body="Hello there",
        ),
    }

    with patch("praxis.graph.nodes.UmansChatModel.create", return_value=mock_model):
        result = await triage_node(
            state,
            config={"configurable": {}},
        )

    triage = result["triage_result"]
    assert triage is not None
    assert triage.priority == Priority.NORMAL
    assert triage.intent == Intent.GENERAL_INQUIRY
    assert triage.confidence == 0.0


@pytest.mark.asyncio
async def test_triage_node_strips_markdown_fence() -> None:
    """The triage node strips ```json fences from the model output."""
    expected = EmailTriage(
        priority=Priority.LOW,
        intent=Intent.GENERAL_INQUIRY,
        sentiment=Sentiment.POSITIVE,
        is_spam=False,
        sender_vip=False,
        confidence=0.7,
    )
    mock_model = _make_mock_model(f"```json\n{expected.model_dump_json()}\n```")

    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="user@example.com",
            subject="Hello",
            body="Hello",
        ),
    }

    with patch("praxis.graph.nodes.UmansChatModel.create", return_value=mock_model):
        result = await triage_node(
            state,
            config={"configurable": {}},
        )

    assert result["triage_result"].priority == Priority.LOW
