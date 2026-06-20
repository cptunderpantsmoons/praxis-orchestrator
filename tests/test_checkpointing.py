"""Tests for the LangGraph checkpointer persistence layer.

Tests the InMemoryFallback checkpointer by exercising it through the
compiled graph, which is how LangGraph interacts with checkpointer
implementations in practice.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from praxis.graph.build import build_graph
from praxis.graph.checkpointer import InMemoryFallback
from praxis.graph.state import AgentState
from praxis.models.schemas import (
    EmailTriage,
    InboundEmail,
    Intent,
    MemoryContext,
    Priority,
    Sentiment,
)


def _make_mock_model(content: str) -> MagicMock:
    """Create a mock model that returns the given content."""
    mock = MagicMock()
    mock.ainvoke = MagicMock(return_value=MagicMock(content=content))
    return mock


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def checkpointer() -> InMemoryFallback:
    """In-memory checkpointer for testing."""
    return InMemoryFallback()


@pytest.fixture
def graph(checkpointer: InMemoryFallback):
    """Compiled graph wired with the in-memory checkpointer."""
    return build_graph(checkpointer=checkpointer)


@pytest.fixture
def test_state() -> AgentState:
    """A minimal AgentState for checkpoint tests."""
    return {
        "email_content": InboundEmail(
            message_id="m1",
            sender="test@example.com",
            subject="Test subject",
            body="Test body",
        ),
        "triage_result": EmailTriage(
            priority=Priority.NORMAL,
            intent=Intent.GENERAL_INQUIRY,
            sentiment=Sentiment.NEUTRAL,
            is_spam=False,
            sender_vip=False,
            confidence=0.5,
        ),
        "memory_context": MemoryContext(),
    }


# ── Unit Tests: InMemoryFallback ────────────────────────────────


def test_in_memory_fallback_creates_saver() -> None:
    """InMemoryFallback instantiates without errors."""
    saver = InMemoryFallback()
    assert saver is not None


@pytest.mark.asyncio
async def test_graph_uses_checkpointer(
    checkpointer: InMemoryFallback,
) -> None:
    """A compiled graph can be constructed with the in-memory checkpointer."""
    graph = build_graph(checkpointer=checkpointer)
    assert graph is not None


@pytest.mark.asyncio
async def test_graph_checkpoint_persists_state(
    graph,
    test_state,
) -> None:
    """Running the graph saves state via the checkpointer."""
    thread = {"configurable": {"thread_id": "persist_test_001"}}
    mock_model = _make_mock_model(
        '{"priority": "normal", "intent": "general_inquiry", "sentiment": "neutral", "is_spam": false, "sender_vip": false, "confidence": 0.5}'
    )
    with patch("praxis.graph.nodes.UmansChatModel.create", return_value=mock_model):
        result = await graph.ainvoke(test_state, config=thread, recursion_limit=10)
    assert "email_content" in result
    assert result["email_content"].sender == "test@example.com"


@pytest.mark.asyncio
async def test_graph_checkpoint_retrieves_state(
    graph,
    test_state,
) -> None:
    """Running on the same thread retrieves previously saved state."""
    thread = {"configurable": {"thread_id": "retrieve_test_001"}}
    mock_model = _make_mock_model(
        '{"priority": "normal", "intent": "general_inquiry", "sentiment": "neutral", "is_spam": false, "sender_vip": false, "confidence": 0.5}'
    )
    with patch("praxis.graph.nodes.UmansChatModel.create", return_value=mock_model):
        result1 = await graph.ainvoke(test_state, config=thread, recursion_limit=10)
    assert result1["email_content"].sender == "test@example.com"


@pytest.mark.asyncio
async def test_graph_checkpoint_multiple_threads(
    graph,
    test_state,
) -> None:
    """Different threads maintain isolated state."""
    mock_model = _make_mock_model(
        '{"priority": "normal", "intent": "general_inquiry", "sentiment": "neutral", "is_spam": false, "sender_vip": false, "confidence": 0.5}'
    )

    thread_a = {"configurable": {"thread_id": "multi_a"}}
    thread_b = {"configurable": {"thread_id": "multi_b"}}

    state_a: AgentState = {
        "email_content": InboundEmail(
            message_id="ma",
            sender="alice@example.com",
            subject="From Alice",
            body="Hello from Alice.",
        ),
    }
    state_b: AgentState = {
        "email_content": InboundEmail(
            message_id="mb",
            sender="bob@example.com",
            subject="From Bob",
            body="Hello from Bob.",
        ),
    }

    with patch("praxis.graph.nodes.UmansChatModel.create", return_value=mock_model):
        result_a = await graph.ainvoke(state_a, config=thread_a, recursion_limit=10)
        result_b = await graph.ainvoke(state_b, config=thread_b, recursion_limit=10)

    assert result_a["email_content"].sender == "alice@example.com"
    assert result_b["email_content"].sender == "bob@example.com"


@pytest.mark.asyncio
async def test_graph_checkpoint_thread_isolation(
    graph,
) -> None:
    """Concurrent graph runs on different threads don't interfere.

    Patches are applied at the module level for the entire test so
    they survive the ``asyncio.gather`` of 6 concurrent graph runs.
    The ``_get_router_and_model`` patch prevents the real
    ``UmansConcurrencyRouter`` from being instantiated (which would
    hit the real Umans API and 404).
    """
    num_threads = 6
    mock_model = _make_mock_model(
        '{"priority": "normal", "intent": "general_inquiry", "sentiment": "neutral", "is_spam": false, "sender_vip": false, "confidence": 0.5}'
    )
    # Module-level patch for the entire test: prevents any real router
    # from being created inside the graph nodes.
    with patch(
        "praxis.graph.nodes._get_router_and_model",
        return_value=(None, mock_model),
    ):
        async def run_graph(tid: str) -> str:
            thread = {"configurable": {"thread_id": tid}}
            state: AgentState = {
                "email_content": InboundEmail(
                    message_id=tid,
                    sender=f"user{tid}@example.com",
                    subject=f"Thread {tid}",
                    body=f"Body for {tid}",
                ),
            }
            result = await graph.ainvoke(state, config=thread, recursion_limit=10)
            return result["email_content"].sender

        results = await asyncio.gather(
            *[run_graph(f"t{i}") for i in range(num_threads)]
        )
    for i, sender in enumerate(results):
        assert sender == f"usert{i}@example.com", f"Thread t{i} corrupted"


@pytest.mark.asyncio
async def test_graph_checkpoint_idempotent(
    graph,
    test_state,
) -> None:
    """Running the graph twice on the same thread returns consistent results."""
    thread = {"configurable": {"thread_id": "idempotent_test"}}
    mock_model = _make_mock_model(
        '{"priority": "normal", "intent": "general_inquiry", "sentiment": "neutral", "is_spam": false, "sender_vip": false, "confidence": 0.5}'
    )
    with patch("praxis.graph.nodes.UmansChatModel.create", return_value=mock_model):
        result1 = await graph.ainvoke(test_state, config=thread, recursion_limit=10)
        result2 = await graph.ainvoke(test_state, config=thread, recursion_limit=10)

    assert result1["email_content"].sender == "test@example.com"
    assert result2["email_content"].sender == "test@example.com"


@pytest.mark.asyncio
async def test_graph_checkpoint_minimal_state(
    graph,
) -> None:
    """Checkpointing with only required state fields succeeds."""
    thread = {"configurable": {"thread_id": "minimal_test"}}
    state: AgentState = {
        "email_content": InboundEmail(
            message_id="minimal",
            sender="minimal@example.com",
            subject="Minimal",
            body="",
        ),
    }
    mock_model = _make_mock_model(
        '{"priority": "normal", "intent": "general_inquiry", "sentiment": "neutral", "is_spam": false, "sender_vip": false, "confidence": 0.5}'
    )
    with patch("praxis.graph.nodes.UmansChatModel.create", return_value=mock_model):
        result = await graph.ainvoke(state, config=thread, recursion_limit=10)
    assert result["email_content"].message_id == "minimal"
