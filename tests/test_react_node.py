"""Tests for the ReAct Orchestrator Node and dummy tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from praxis.graph.nodes import react_node
from praxis.graph.state import AgentState
from praxis.models.schemas import (
    CalculationResult,
    EmailTriage,
    InboundEmail,
    Intent,
    MemoryContext,
    Priority,
    SearchResult,
    Sentiment,
)


def _make_mock_model(content: str) -> MagicMock:
    """Create a mock model that returns the given content."""
    mock = MagicMock()
    mock.ainvoke = MagicMock(return_value=MagicMock(content=content))
    return mock


def _make_mock_search_result(query: str) -> SearchResult:
    return SearchResult(
        query=query,
        results=["PRAXIS Status: System is operational."],
    )


def _make_mock_calc_result(expression: str, value: float) -> CalculationResult:
    return CalculationResult(
        expression=expression,
        value=value,
        unit="",
    )


@pytest.mark.asyncio
async def test_react_node_invokes_dummy_search() -> None:
    """ReAct loop calls dummy_search and returns a structured SearchResult."""
    mock_model = _make_mock_model(
        "TOOL:dummy_search(query='PRAXIS status')\nFINAL: The system is operational."
    )
    mock_search_result = _make_mock_search_result("PRAXIS status")

    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="user@example.com",
            subject="Status",
            body="What is the system status?",
        ),
        "triage_result": EmailTriage(
            priority=Priority.NORMAL,
            intent=Intent.GENERAL_INQUIRY,
            sentiment=Sentiment.NEUTRAL,
            is_spam=False,
            sender_vip=False,
            confidence=0.9,
        ),
        "memory_context": MemoryContext(),
    }

    with (
        patch("praxis.graph.nodes.UmansChatModel.create", return_value=mock_model),
        patch("praxis.graph.nodes.dummy_search", MagicMock(ainvoke=MagicMock(return_value=mock_search_result))),
    ):
        result = await react_node(
            state,
            config={"configurable": {}},
            max_iterations=3,
        )

    tool_outputs = result["tool_outputs"]
    assert "dummy_search" in tool_outputs
    assert isinstance(tool_outputs["dummy_search"], SearchResult)
    assert tool_outputs["dummy_search"].query == "PRAXIS status"
    assert "operational" in result["final_response"]


@pytest.mark.asyncio
async def test_react_node_invokes_dummy_calculator() -> None:
    """ReAct loop calls dummy_calculator and returns a structured CalculationResult."""
    mock_model = _make_mock_model(
        "TOOL:dummy_calculator(expression='2 + 3')\nFINAL: The answer is five."
    )
    mock_calc_result = _make_mock_calc_result("2 + 3", 5.0)

    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="user@example.com",
            subject="Calc",
            body="What is 2+3?",
        ),
        "triage_result": EmailTriage(
            priority=Priority.NORMAL,
            intent=Intent.GENERAL_INQUIRY,
            sentiment=Sentiment.NEUTRAL,
            is_spam=False,
            sender_vip=False,
            confidence=0.9,
        ),
        "memory_context": MemoryContext(),
    }

    with (
        patch("praxis.graph.nodes.UmansChatModel.create", return_value=mock_model),
        patch("praxis.graph.nodes.dummy_calculator", MagicMock(ainvoke=MagicMock(return_value=mock_calc_result))),
    ):
        result = await react_node(
            state,
            config={"configurable": {}},
            max_iterations=3,
        )

    tool_outputs = result["tool_outputs"]
    assert "dummy_calculator" in tool_outputs
    assert isinstance(tool_outputs["dummy_calculator"], CalculationResult)
    assert tool_outputs["dummy_calculator"].value == 5.0
    assert "five" in result["final_response"]


@pytest.mark.asyncio
async def test_react_node_returns_final_without_tool() -> None:
    """ReAct node returns the model's FINAL line without invoking any tool."""
    mock_model = _make_mock_model("FINAL: I will handle this manually.")

    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="user@example.com",
            subject="Help",
            body="Help",
        ),
        "triage_result": EmailTriage(
            priority=Priority.NORMAL,
            intent=Intent.GENERAL_INQUIRY,
            sentiment=Sentiment.NEUTRAL,
            is_spam=False,
            sender_vip=False,
            confidence=0.9,
        ),
        "memory_context": MemoryContext(),
    }

    with patch("praxis.graph.nodes.UmansChatModel.create", return_value=mock_model):
        result = await react_node(
            state,
            config={"configurable": {}},
            max_iterations=3,
        )

    assert result["tool_outputs"] == {}
    assert result["final_response"] == "I will handle this manually."
