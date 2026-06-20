"""Tests for the tool registry.

Tests that search_tool, calculator_tool, and email tools work correctly
and that ALL_TOOLS registry is correct.
"""

from __future__ import annotations

import pytest

from praxis.tools.email_tools import (
    EMAIL_TOOLS,
    reply_email_tool,
    send_email_tool,
)
from praxis.tools.stub_tools import (
    ALL_TOOLS,
    calculator_tool,
    dummy_tool,
    search_tool,
)

# ── search_tool tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_tool_returns_results() -> None:
    """search_tool returns mock search results."""
    result = await search_tool.ainvoke({"query": "praxis email guide"})
    assert isinstance(result, dict)
    assert "results" in result
    assert len(result["results"]) >= 1


@pytest.mark.asyncio
async def test_search_tool_handles_edge_cases() -> None:
    """search_tool handles various input types gracefully."""
    result = await search_tool.ainvoke({"query": ""})
    assert isinstance(result, dict)
    assert "results" in result


# ── calculator_tool tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_calculator_tool_success() -> None:
    """calculator_tool returns correct result for valid expressions."""
    result = await calculator_tool.ainvoke({"expression": "2 + 3"})
    assert isinstance(result, dict)
    assert "result" in result
    assert result["result"] == 5


@pytest.mark.asyncio
async def test_calculator_tool_complex_expression() -> None:
    """calculator_tool handles complex arithmetic."""
    # (10 * 3) - (2 ** 4) = 30 - 16 = 14
    result = await calculator_tool.ainvoke({"expression": "(10 * 3) - (2 ** 4)"})
    assert isinstance(result, dict)
    assert result["result"] == 14


@pytest.mark.asyncio
async def test_calculator_tool_division() -> None:
    """calculator_tool handles division correctly."""
    result = await calculator_tool.ainvoke({"expression": "10 / 2"})
    assert result["result"] == 5.0


# ── dummy_tool tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dummy_tool_echoes_input() -> None:
    """dummy_tool returns the input unchanged."""
    result = await dummy_tool.ainvoke({"input_text": "test input"})
    assert result == "test input"


# ── email_tools tests ────────────────────────────────────────────


def test_send_email_tool_requires_args() -> None:
    """send_email_tool is an async StructuredTool with the right schema."""
    assert send_email_tool.name == "send_email"
    assert "coroutine" in dir(send_email_tool) or hasattr(send_email_tool, "coroutine")
    props = send_email_tool.args_schema.model_json_schema()["properties"]
    assert "to" in props
    assert "subject" in props
    assert "body" in props


def test_reply_email_tool_requires_args() -> None:
    """reply_email_tool is an async StructuredTool with inbox_id, message_id, body."""
    assert reply_email_tool.name == "reply_email"
    props = reply_email_tool.args_schema.model_json_schema()["properties"]
    assert "inbox_id" in props
    assert "message_id" in props
    assert "body" in props


# ── ALL_TOOLS registry tests ─────────────────────────────────────


def test_all_tools_contains_search() -> None:
    """ALL_TOOLS contains search_tool."""
    tool_names = [t.name for t in ALL_TOOLS]
    assert "search_tool" in tool_names


def test_all_tools_contains_calculator() -> None:
    """ALL_TOOLS contains calculator_tool."""
    tool_names = [t.name for t in ALL_TOOLS]
    assert "calculator_tool" in tool_names


def test_all_tools_contains_dummy() -> None:
    """ALL_TOOLS contains dummy_tool."""
    tool_names = [t.name for t in ALL_TOOLS]
    assert "dummy_tool" in tool_names


def test_all_tools_contains_email_tools() -> None:
    """ALL_TOOLS contains both email tools (new async StructuredTool design)."""
    tool_names = [t.name for t in ALL_TOOLS]
    assert "send_email" in tool_names
    assert "reply_email" in tool_names


def test_all_tools_list() -> None:
    """ALL_TOOLS is a list of exactly 5 tools."""
    assert isinstance(ALL_TOOLS, list)
    assert len(ALL_TOOLS) == 5


def test_email_tools_list() -> None:
    """EMAIL_TOOLS now includes send + reply + search + list_threads (4 tools)."""
    assert isinstance(EMAIL_TOOLS, list)
    assert len(EMAIL_TOOLS) == 4
    names = {t.name for t in EMAIL_TOOLS}
    assert names == {"send_email", "reply_email", "search_inbox", "list_threads"}
