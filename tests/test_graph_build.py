"""Tests for the Phase 2 LangGraph build and conditional routing."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from praxis.graph import build_graph
from praxis.graph.build import _route_triage
from praxis.graph.state import AgentState
from praxis.models.schemas import (
    EmailTriage,
    InboundEmail,
    Intent,
    Priority,
    Sentiment,
)


def _fake_umans_model(content: str) -> MagicMock:
    """Return a mock LangChain model whose ainvoke returns the given content."""
    model = MagicMock()
    model.ainvoke = MagicMock(return_value=AIMessage(content=content))
    return model


def _make_router_for_model(content: str) -> MagicMock:
    """Return a mock router whose create() returns a fake model."""
    router = MagicMock()
    model = _fake_umans_model(content)
    router.create.return_value = model
    return router


def test_route_triage_spam() -> None:
    """Spam triage routes to the discard node."""
    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1", sender="spammer@example.com", subject="Spam", body="Buy now"
        ),
        "triage_result": EmailTriage(
            priority=Priority.LOW,
            intent=Intent.SPAM,
            sentiment=Sentiment.NEUTRAL,
            is_spam=True,
            sender_vip=False,
            confidence=0.99,
        ),
    }
    assert _route_triage(state) == "discard"


def test_route_triage_correction() -> None:
    """Correction intent routes to the correction stub node."""
    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="user@example.com",
            subject="Correction",
            body="Correction: always CC legal",
        ),
        "triage_result": EmailTriage(
            priority=Priority.NORMAL,
            intent=Intent.CORRECTION,
            sentiment=Sentiment.NEUTRAL,
            is_spam=False,
            sender_vip=False,
            confidence=0.9,
        ),
    }
    assert _route_triage(state) == "correction_node"


def test_route_triage_standard() -> None:
    """Standard email routes to context loading / react."""
    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="user@example.com",
            subject="Question",
            body="What is the status?",
        ),
        "triage_result": EmailTriage(
            priority=Priority.NORMAL,
            intent=Intent.GENERAL_INQUIRY,
            sentiment=Sentiment.NEUTRAL,
            is_spam=False,
            sender_vip=False,
            confidence=0.8,
        ),
    }
    assert _route_triage(state) == "context_loading"


@pytest.mark.asyncio
async def test_graph_runs_end_to_end_with_mocked_models(monkeypatch: Any) -> None:
    """The compiled graph executes triage, context loading, react, and returns state."""
    from praxis import graph as graph_module

    triage_return = EmailTriage(
        priority=Priority.NORMAL,
        intent=Intent.GENERAL_INQUIRY,
        sentiment=Sentiment.POSITIVE,
        is_spam=False,
        sender_vip=False,
        confidence=0.92,
    )
    triage_json = triage_return.model_dump_json()

    # Patch both model slots used by the graph
    call_count = [0]

    def _mock_get_router(model_name: str, router: Any = None) -> tuple[Any, Any]:
        from langchain_core.messages import AIMessage

        call_count[0] += 1
        fake_model = MagicMock()
        if call_count[0] == 1:
            # First call = triage
            fake_model.ainvoke = MagicMock(return_value=AIMessage(content=triage_json))
        else:
            # Subsequent calls = react
            fake_model.ainvoke = MagicMock(
                return_value=AIMessage(content="FINAL: I will check that for you.")
            )
        return router, fake_model

    monkeypatch.setattr(graph_module.nodes, "_get_router_and_model", _mock_get_router)

    graph = build_graph()
    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="user@example.com",
            subject="Question",
            body="What is the status?",
        ),
    }

    result = await graph.ainvoke(
        state,
        config={"configurable": {"thread_id": "thread-123"}},
    )

    assert result["triage_result"] is not None
    assert result["triage_result"].priority == Priority.NORMAL
    assert result["final_response"].startswith("I will check that for you.")
    assert result["metadata"].model_calls["qwen"] == 1


@pytest.mark.asyncio
async def test_graph_discards_spam(monkeypatch: Any) -> None:
    """Spam email ends at the discard node."""
    from praxis import graph as graph_module

    triage_json = EmailTriage(
        priority=Priority.LOW,
        intent=Intent.SPAM,
        sentiment=Sentiment.NEUTRAL,
        is_spam=True,
        sender_vip=False,
        confidence=0.99,
    ).model_dump_json()

    def _mock_get_router(model_name: str, router: Any = None) -> tuple[Any, Any]:
        from langchain_core.messages import AIMessage

        fake_model = MagicMock()
        fake_model.ainvoke = MagicMock(return_value=AIMessage(content=triage_json))
        return router, fake_model

    monkeypatch.setattr(graph_module.nodes, "_get_router_and_model", _mock_get_router)

    graph = build_graph()
    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="spammer@example.com",
            subject="Spam",
            body="Buy cheap meds",
        ),
    }

    result = await graph.ainvoke(
        state,
        config={"configurable": {"thread_id": "thread-spam"}},
    )

    assert result["triage_result"].is_spam is True
    assert "spam" in (result.get("final_response") or "").lower()
