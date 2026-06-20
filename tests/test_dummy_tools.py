"""Tests for the REQ-205 typed-Pydantic ReAct tools.

These tests cover ``dummy_search`` and ``dummy_calculator`` directly.
They were the missing names that caused 9 test files to fail at import
time during the Phase 2 resumption. They are the spec-mandated tools
for the ReAct orchestrator (see phase2_requirements.md REQ-205).
"""

from __future__ import annotations

import pytest

from praxis.models.schemas import CalculationResult, SearchResult
from praxis.tools.stub_tools import (
    REACT_TOOLS,
    dummy_calculator,
    dummy_search,
)

# ── dummy_search ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dummy_search_returns_search_result_instance() -> None:
    """dummy_search returns a SearchResult, not a dict or string."""
    result = await dummy_search.ainvoke({"query": "PRAXIS status"})
    assert isinstance(result, SearchResult)
    assert result.query == "PRAXIS status"
    assert len(result.results) >= 1
    assert result.result_count == len(result.results)
    assert result.tool_name == "dummy_search"
    assert result.success is True


@pytest.mark.asyncio
async def test_dummy_search_handles_empty_query() -> None:
    """Empty query still returns a valid SearchResult."""
    result = await dummy_search.ainvoke({"query": ""})
    assert isinstance(result, SearchResult)
    assert result.query == ""
    assert result.success is True


@pytest.mark.asyncio
async def test_dummy_search_preserves_query_chars() -> None:
    """SearchResult echoes the original query verbatim."""
    result = await dummy_search.ainvoke({"query": "send + receive * 2"})
    assert result.query == "send + receive * 2"


# ── dummy_calculator ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dummy_calculator_addition() -> None:
    """2 + 3 = 5.0 (float return type)."""
    result = await dummy_calculator.ainvoke({"expression": "2 + 3"})
    assert isinstance(result, CalculationResult)
    assert result.value == 5.0
    assert result.unit == ""
    assert result.success is True
    assert result.tool_name == "dummy_calculator"


@pytest.mark.asyncio
async def test_dummy_calculator_complex_expression() -> None:
    """(10 * 3) - (2 ** 4) = 30 - 16 = 14.0."""
    result = await dummy_calculator.ainvoke({"expression": "(10 * 3) - (2 ** 4)"})
    assert isinstance(result, CalculationResult)
    assert result.value == 14.0


@pytest.mark.asyncio
async def test_dummy_calculator_division() -> None:
    """Division returns a float."""
    result = await dummy_calculator.ainvoke({"expression": "10 / 2"})
    assert result.value == 5.0


@pytest.mark.asyncio
async def test_dummy_calculator_unary_negation() -> None:
    """Unary negation works."""
    result = await dummy_calculator.ainvoke({"expression": "-5 + 10"})
    assert result.value == 5.0


@pytest.mark.asyncio
async def test_dummy_calculator_invalid_expression_returns_zero() -> None:
    """Invalid expressions yield a zero-valued CalculationResult with an
    error message in ``unit`` — never a crash, never a raw string."""
    result = await dummy_calculator.ainvoke({"expression": "import os"})
    assert isinstance(result, CalculationResult)
    assert result.value == 0.0
    assert result.unit.startswith("error")
    assert result.success is True  # The ToolOutput contract stays green


@pytest.mark.asyncio
async def test_dummy_calculator_attribute_access_blocked() -> None:
    """Attribute access (e.g. ``__import__``) is structurally rejected."""
    result = await dummy_calculator.ainvoke({"expression": "().__class__"})
    assert result.value == 0.0
    assert result.unit.startswith("error")


# ── REACT_TOOLS registry ────────────────────────────────────────


def test_react_tools_contains_dummy_search() -> None:
    names = [t.name for t in REACT_TOOLS]
    assert "dummy_search" in names


def test_react_tools_contains_dummy_calculator() -> None:
    names = [t.name for t in REACT_TOOLS]
    assert "dummy_calculator" in names


def test_react_tools_size_is_exactly_two() -> None:
    """REQ-205: at least two tools in the stub registry."""
    assert len(REACT_TOOLS) == 2
